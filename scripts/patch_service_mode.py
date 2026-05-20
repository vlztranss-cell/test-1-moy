"""
Точечный патч: добавить `service_mode: 'public'` в payload PiAPI Create Task,
чтобы Kling шёл по PAYG (общий баланс $55), а не по host-your-account (0/0).

После запуска новые генерации начнут списываться из основного баланса PiAPI.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "9LfeKp4vBn5ZTV2D"

# Новый JS для After Upload — добавлен `service_mode: 'public'`
NEW_JS = (
    "const r=$('Upload to Freeimage').first().json;"
    "const url=r.image?.url||null;"
    "if(!url)return[{json:{error:'Upload failed: '+JSON.stringify(r).substring(0,100),status:'error'}}];"
    "const p=$('Prepare').first().json;"
    "return[{json:{image_url:url,prompt:p.prompt,session_id:p.session_id,"
    "piapi_payload:JSON.stringify({"
    "model:'kling',"
    "task_type:'video_generation',"
    "service_mode:'public',"  # ← новое: PAYG вместо host-your-account
    "input:{image_url:url,prompt:p.prompt,cfg_scale:0.5,duration:5,aspect_ratio:'9:16',mode:'std',version:'2.5'}"
    "})}}];"
)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()

    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{WORKFLOW_ID}"
    req = urllib.request.Request(url, headers={"X-N8N-API-KEY": env["N8N_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        wf = json.loads(r.read())

    # Меняем только jsCode у After Upload
    changed = False
    for n in wf["nodes"]:
        if n["name"] == "After Upload":
            n["parameters"]["jsCode"] = NEW_JS
            changed = True
            break

    if not changed:
        print("[ERR] After Upload не найдена")
        sys.exit(1)

    body = {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": wf.get("settings", {"executionOrder": "v1"}),
    }
    data = json.dumps(body).encode("utf-8")
    put = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "X-N8N-API-KEY": env["N8N_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(put, timeout=30) as r:
        res = json.loads(r.read())

    print(f"[OK] After Upload пропатчен. service_mode='public'")
    print(f"     версия workflow: {res.get('versionId', 'n/a')[:8]}")
    print(f"     следующие генерации пойдут через PAYG (общий баланс $55)")


if __name__ == "__main__":
    main()
