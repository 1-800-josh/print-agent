# Print Agent Deployment Guide

## Prerequisites

- Python 3.9+
- Network access to internal portal API
- Write access to configured network paths
- API key with service permissions

## Installation

### Step 1: Extract/Clone

```bash
cd /opt
sudo git clone <repo-url> print-agent
sudo chown -R printagent:printagent print-agent
```

### Step 2: Create User

```bash
sudo useradd -r -s /bin/false printagent
```

### Step 3: Virtual Environment

```bash
cd /opt/print-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 4: Configuration

```bash
sudo nano /opt/print-agent/.env
```

Add:

```env
API_BASE_URL=https://portal.yourcompany.com
API_KEY=your-service-api-key
ORGANISATION_ID=your-org-uuid
UPLOADTHING_APP_ID=your-app-id
SYNC_INTERVAL_SECONDS=60
MAX_WORKERS=10
```

### Step 5: Systemd Service

```bash
sudo nano /etc/systemd/system/print-agent.service
```

Content:

```ini
[Unit]
Description=Print Agent Sync Service
After=network.target

[Service]
Type=simple
User=printagent
Group=printagent
WorkingDirectory=/opt/print-agent
Environment=PYTHONPATH=/opt/print-agent
EnvironmentFile=/opt/print-agent/.env
ExecStart=/opt/print-agent/venv/bin/python main.py service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Step 6: Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable print-agent
sudo systemctl start print-agent

# Check status
sudo systemctl status print-agent
sudo journalctl -u print-agent -f
```

## Windows Deployment

### Step 1: Install Python

Install Python 3.9+ from python.org

### Step 2: Extract

Extract to `C:\PrintAgent`

### Step 3: Virtual Environment

```batch
cd C:\PrintAgent
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Step 4: Configuration

Create `C:\PrintAgent\.env` with settings

### Step 5: NSSM Service

Download NSSM from https://nssm.cc/

```batch
nssm install PrintAgent
# Set:
# Path: C:\PrintAgent\venv\Scripts\python.exe
# Startup directory: C:\PrintAgent
# Arguments: main.py service

nssm set PrintAgent AppEnvironmentExtra PYTHONPATH=C:\PrintAgent
nssm set PrintAgent AppEnvironmentExtra + API_BASE_URL=https://...
nssm set PrintAgent AppEnvironmentExtra + API_KEY=...

nssm start PrintAgent
```

## Troubleshooting

### Check Logs

```bash
# Linux
sudo journalctl -u print-agent -n 100

# Windows
type C:\ProgramData\PrintAgent\logs\sync_service.log
```

### Test Sync

```bash
python main.py sync
```

### Verify API Access

```bash
curl -H "x-api-key: YOUR_KEY" \
  https://portal/api/organisations/ORG_ID/network-path-configs
```
