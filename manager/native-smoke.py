#!/usr/bin/env python3
"""Probe actual OMP RPC extension/schema loading without sending any prompt."""
import argparse
import json
import os
import pathlib
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time

EXPECTED_FIELDS = {
    "workflow": ("action",),
    "laya_workflow": ("action",),
    "workflow_plan": ("action",),
    "workflow_check": ("id", "checkId"),
    "laya_batch": ("recipe", "objective", "items"),
    "durindoor_status": (),
}

def check_wire_schema(name, schema):
    """Check wire roots, schema containers and local refs on every host."""
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        raise RuntimeError(name + ": provider wire schema must be an object with properties")
    if not all(field in schema["properties"] for field in EXPECTED_FIELDS[name]):
        raise RuntimeError(name + ": expected wire properties missing")
    def walk(value):
        if isinstance(value, bool):
            return
        if not isinstance(value, dict):
            raise RuntimeError(name + ": malformed schema node")
        if "type" in value:
            types = value["type"] if isinstance(value["type"], list) else [value["type"]]
            if not types or any(item not in ("null", "boolean", "object", "array", "number", "integer", "string") for item in types):
                raise RuntimeError(name + ": invalid schema type")
        required = value.get("required", [])
        if not isinstance(required, list) or any(not isinstance(field, str) for field in required) or len(required) != len(set(required)):
            raise RuntimeError(name + ": malformed required fields")
        if "properties" in value and any(field not in value["properties"] for field in required):
            raise RuntimeError(name + ": required field lacks a property schema")
        if "$ref" in value:
            reference = value["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise RuntimeError(name + ": unsupported external wire schema reference")
            target = schema
            try:
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                raise RuntimeError(name + ": unresolved local schema reference")
        for keyword in ("properties", "$defs", "definitions", "patternProperties"):
            if keyword in value:
                if not isinstance(value[keyword], dict):
                    raise RuntimeError(name + ": malformed " + keyword)
                for child in value[keyword].values():
                    walk(child)
        for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
            if keyword in value:
                if not isinstance(value[keyword], list) or not value[keyword]:
                    raise RuntimeError(name + ": malformed " + keyword)
                for child in value[keyword]:
                    walk(child)
        for keyword in ("items", "additionalProperties", "not", "if", "then", "else", "contains"):
            if keyword in value:
                walk(value[keyword])
    walk(schema)
    try:
        import jsonschema
    except ImportError:
        return False
    jsonschema.validators.validator_for(schema).check_schema(schema)
    return True

parser = argparse.ArgumentParser()
parser.add_argument("framework", nargs="?", help="Built framework index.js")
parser.add_argument("--adapter-only", action="store_true")
parser.add_argument("--adapter", default=str(pathlib.Path.home() / ".omp/agent/extensions/pi-workflows-omp.ts"))
parser.add_argument("--runtime", default=str(pathlib.Path.home() / ".omp/agent/lib/pi-workflows-omp/runtime"))
parser.add_argument("--plugin", required=True, help="Built Laya skills plugin entry")
parser.add_argument("--reporter", required=True, help="Built Crew metadata reporter")
args = parser.parse_args()
if not args.adapter_only and not args.framework:
    raise RuntimeError("Provide a framework bundle or --adapter-only")
omp = shutil.which("omp")
node = shutil.which("node")
if not omp or not node:
    raise RuntimeError("omp and node are required")
runtime = pathlib.Path(args.runtime).resolve()
if "PI_WORKFLOWS_OMP_DATABASE" not in (runtime / "dist/state/database.js").read_text():
    raise RuntimeError("Reinstall the prepared adapter with isolated database override before testing")
events = queue.Queue()
with tempfile.TemporaryDirectory(prefix="pi-omp-rpc-") as temporary:
    root = pathlib.Path(temporary)
    database = root / "workflow-state.sqlite"
    env = {k:v for k,v in os.environ.items() if not k.startswith("HERDR_")}
    env["PI_CODING_AGENT_DIR"] = str(root / "agent")
    env["PI_WORKFLOWS_OMP_DATABASE"] = str(database)
    env["PI_WORKFLOWS_OMP_NODE"] = node
    env["OMP_WORKFLOW_PROFILE"] = str(root / "nonexistent-profile")
    agent = root / "agent"
    agent.mkdir()
    # Select a fixture model without reading production credentials or making
    # inference. An accidental request would fail against this closed port.
    (agent / "models.yml").write_text(json.dumps({"providers": {"durindoor": {"baseUrl": "http://127.0.0.1:9/v1", "apiKey": "no-generation-smoke-fixture", "api": "openai-completions", "models": [{"id": "no-generation", "name": "No generation smoke fixture", "reasoning": False, "input": ["text"], "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "contextWindow": 8192, "maxTokens": 128}]}}}))
    command = [omp, "--mode", "rpc", "--no-session", "--no-extensions", "--provider", "durindoor", "--model", "no-generation", "--tools", "workflow" if args.adapter_only else "workflow,laya_workflow,workflow_check,workflow_plan,laya_batch,durindoor_status", "-e", str(pathlib.Path(args.adapter).resolve())]
    if not args.adapter_only:
        command.extend(["-e", str(pathlib.Path(args.framework).resolve())])
    command.extend(["-e", str(pathlib.Path(args.plugin).resolve()), "-e", str(pathlib.Path(args.reporter).resolve())])
    process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    def consume(stream, kind):
        for line in stream:
            events.put((kind, line))
    threads = [threading.Thread(target=consume, args=(stream, kind), daemon=True) for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr"))]
    for thread in threads:
        thread.start()
    responses = {}
    errors = []
    diagnostics = []
    try:
        for command in ("get_available_commands", "get_state"):
            process.stdin.write(json.dumps({"type": command, "id": "smoke-" + command}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not all(command in responses for command in ("get_available_commands", "get_state")):
            try:
                kind, line = events.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if "Failed to load extension" in line or "variant.properties.action" in line:
                errors.append("Extension load/schema failure")
            if kind != "stdout":
                diagnostics.append(line.strip()[:500])
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if value.get("type") == "response":
                responses[value.get("command", value.get("type"))] = value
            if value.get("type") == "extension_error":
                errors.append("Extension runtime failure")
                diagnostics.append(str(value.get("error", ""))[:500])
        state = responses.get("get_state")
        if errors or not state or not state.get("success"):
            raise RuntimeError("Actual OMP RPC startup/schema loading failed; exit=" + str(process.poll()) + "; diagnostics=" + json.dumps(diagnostics[-8:]))
        data = state["data"]
        tools = data.get("dumpTools", [])
        names = [tool["name"] for tool in tools]
        expected = {"workflow"} if args.adapter_only else set(EXPECTED_FIELDS)
        if set(names) != expected or len(names) != len(set(names)):
            raise RuntimeError("Expected exact active tool set with unique names: " + ",".join(names))
        metaschema = [check_wire_schema(tool["name"], tool.get("parameters")) for tool in tools]
        if data.get("isStreaming") or data.get("messageCount", 0) > 0:
            raise RuntimeError("A model turn or conversation message unexpectedly started")
        commands = responses.get("get_available_commands", {})
        command_names = [item.get("name") for item in commands.get("data", {}).get("commands", [])]
        required_commands = ("workflow",) if args.adapter_only else ("workflow", "laya-flow", "laya-skills")
        if not commands.get("success") or not all(name in command_names for name in required_commands):
            raise RuntimeError("Combined adapter/framework slash commands were not registered")
        print(json.dumps({"status": "passed", "actualOmpRpc": True, "adapterOnly": args.adapter_only, "workflowWireSchema": "object", "activeTools": sorted(names), "uniqueActiveTools": len(names), "allWireSchemaRoots": "object", "schemaStructureAndLocalRefs": "passed", "jsonSchemaMetaschemaValidation": all(metaschema), "commandProbe": commands.get("success", False), "modelRequests": 0, "isolatedAgentDirectory": True, "isolatedWorkflowDatabase": True}))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        cleanup = 'import {pathToFileURL} from "node:url"; const {WorkflowClient}=await import(pathToFileURL(process.argv[1]+"/dist/client/client.js").href); const c=new WorkflowClient({databasePath:process.argv[2]}); try {await c.request({operation:"server.stop"});} catch {} finally {await c.close();}'
        subprocess.run([node, "--input-type=module", "-e", cleanup, str(runtime), str(database)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
