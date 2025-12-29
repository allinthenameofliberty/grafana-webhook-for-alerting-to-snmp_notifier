#!/usr/bin/env python3
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
import re
import configparser
import os

# ---- Load config ----
config_path = os.path.join(os.path.dirname(__file__), "config.ini")
config = configparser.ConfigParser()
config.read(config_path)

SNMP_NOTIFIER_URL = config.get("settings", "snmp_notifier_url")
NODE_FIELDS = [f.strip() for f in config.get("settings", "node_fields").split(",")]

def iso_now():
    return datetime.now(timezone.utc).isoformat()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        grafana = json.loads(body.decode("utf-8"))

        # ---- Convert Grafana legacy state → Alertmanager state ----
        status = "firing" if grafana.get("state") == "alerting" else "resolved"

        # ---- Base labels ----
        labels = {
            "alertname": grafana.get("ruleName", "grafana-alert"),
            "state": grafana.get("state", ""),
            "ruleId": str(grafana.get("ruleId", "")),
            "dashboardId": str(grafana.get("dashboardId", "")),
            "panelId": str(grafana.get("panelId", "")),
            "orgId": str(grafana.get("orgId", "")),
        }

        # ---- Extract evalMatches safely ----
        eval_matches = grafana.get("evalMatches", [])
        if eval_matches:
            match0 = eval_matches[0]
            labels["metric"] = match0.get("metric", "")
            tags = match0.get("tags", {})
        else:
            tags = {}

        metric_str = labels.get("metric", "")

        # ---- Populate all node fields dynamically ----
        for field in NODE_FIELDS:
            value = tags.get(field, "") or grafana.get(field, "")
            if not value and metric_str:
                regex = rf'{field}="([^"]+)"'
                m = re.search(regex, metric_str)
                if m:
                    value = m.group(1)
            labels[field] = value

        # ---- Annotations ----
        annotations = {
            "title": grafana.get("title", ""),
            "message": grafana.get("message", ""),
            "ruleUrl": grafana.get("ruleUrl", ""),
            "evalMatches": json.dumps(eval_matches),
            "tags": json.dumps(tags),
            "rawGrafanaJSON": json.dumps(grafana),
        }

        alert = {
            "status": status,
            "labels": labels,
            "annotations": annotations,
            "startsAt": iso_now(),
            "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else iso_now(),
        }

        payload = {
            "receiver": "snmp-notifier",
            "status": status,
            "alerts": [alert],
            "groupLabels": {"alertname": labels["alertname"]},
            "commonLabels": labels,
            "commonAnnotations": annotations,
            "externalURL": "grafana-legacy-proxy",
        }

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            SNMP_NOTIFIER_URL,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            urllib.request.urlopen(req, timeout=5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9093), Handler)
    print("Grafana legacy → Alertmanager proxy listening on :9093")
    server.serve_forever()
