# Print Agent

Background service for automatic print task synchronization with hot folder support.

## Overview

The Print Agent is a Python service that:
- Syncs READY_FOR_PRODUCTION orders from the API to network paths
- Organizes files by material and delivery date
- Uses Morse code encoding for filenames
- Watches hot folders for file operations to update task status
- Emits structured JSON health/state events for external supervision
- Supports multiprocessing for fast downloads

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Internal      │     │   Print Agent    │     │   Hot Folders   │
│   Portal API    │◄───►│   Sync Service   │◄───►│   (Network)     │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Network Path   │     │  File Watcher    │     │  User Actions   │
│  Configurations │     │  (watchdog)      │     │  (move/delete)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd print-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `config.json` file based on `config.example.json`:

```bash
cp config.example.json config.json
# Edit config.json with your values
```

Or use the `--config` flag to specify a custom config file path:

```bash
python main.py --config /path/to/my-config.json service
```

For bootstrap configuration (e.g., when the config file path itself needs to be configured), set the `PRINT_AGENT_CONFIG` environment variable:

```bash
export PRINT_AGENT_CONFIG=/etc/print-agent/config.json
python main.py service
```

### Running

```bash
# Run the sync service (continuous)
python main.py service

# Run one sync cycle
python main.py sync
```

## Features

### Order Sync (Phase 2)

- Fetches READY_FOR_PRODUCTION orders from API
- Filters by materials with configured network paths
- Groups files by material + delivery_date
- Generates Morse code filenames: `{orderId}-{artworkGroupId}-{optionMorseCode}.{ext}`
- Downloads images in parallel using multiprocessing
- Handles filename collisions with counter suffix

### File Watcher (Phase 2)

- Monitors configured network paths
- Debounces rapid file events
- Detects:
  - Move to user folder → Assign task
  - Move out of user folder → Unassign task
  - Delete from user folder → Complete task

### Backend API (Phase 1)

- **Network Path Config API**: CRUD for material → network path mappings
- **Extended Export API**: Includes network paths, artwork options, Morse codes
- **Assignment API**: Assign/unassign/complete endpoints

### Admin UI (Phase 1)

- Configuration page in internal portal
- Form to map materials to network paths
- Validation and testing

## Hot Folder Structure

```
/network-path/
  └── {material}/
      └── {delivery-date}/
          ├── {orderId}-{artworkGroupId}-{morse}.png  # Incoming
          └── {user}/
              └── {orderId}-{artworkGroupId}-{morse}.png  # In Progress
```

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Quality

```bash
# Formatting
black src/ tests/

# Linting
ruff check src/ tests/

# Type checking
mypy src/
```

## Deployment

### Windows Service (NSSM)

```batch
# Install service
nssm install PrintAgent "C:\Python39\python.exe" "C:\PrintAgent\main.py service"
nssm set PrintAgent AppDirectory C:\PrintAgent
nssm set PrintAgent Start SERVICE_AUTO_START

# Start service
nssm start PrintAgent
```

### Linux Systemd

```bash
# Copy service file
sudo cp scripts/print-agent.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start
sudo systemctl enable print-agent
sudo systemctl start print-agent
```

## Configuration Options

Configuration is specified in JSON format. All options are optional unless marked as Required.

| Option | Description | Default | Type |
|--------|-------------|---------|------|
| `API_BASE_URL` | Internal portal API URL | `"http://localhost:3000"` | string |
| `API_KEY` | Service API key | `""` (Required) | string |
| `ORGANISATION_ID` | Organisation UUID | `""` (Required) | string |
| `UPLOADTHING_APP_ID` | UploadThing app ID | `""` (Required) | string |
| `SYNC_INTERVAL_SECONDS` | Seconds between syncs | `60` | number |
| `MAX_WORKERS` | Download worker processes | `10` | number |
| `DOWNLOAD_TIMEOUT_SECONDS` | Download timeout | `60` | number |
| `CONFIG_REFRESH_INTERVAL_SECONDS` | Config refresh interval | `300` | number |
| `FILE_EVENT_DEBOUNCE_SECONDS` | File event debounce time | `2.0` | number |
| `FILE_CLEANUP_INTERVAL_SECONDS` | File cleanup interval | `30` | number |
| `RECONCILE_TASK_STATES` | Enable task state reconciliation | `true` | boolean |
| `CLEANUP_EMPTY_ARTWORK_FOLDERS` | Enable empty folder cleanup | `true` | boolean |
| `NETWORK_DRIVE_PREFIX` | Network drive prefix path | `""` | string |
| `ARTWORK_FOLDER` | Artworks folder name | `"artworks"` | string |
| `USERS_FOLDER` | Users folder name | `"users"` | string |
| `SERVICE_NAME` | Service name for logging | `"PrintAgentSync"` | string |
| `STRUCTURED_STATUS_STDOUT_ENABLED` | Emit machine-readable JSON health events on stdout | `false` | boolean |
| `HEALTH_HEARTBEAT_INTERVAL_SECONDS` | Interval between heartbeat events | `30` | number |
| `LOG_DIR` | Log directory | Platform default | string |
| `RENAME_LOG_DIR` | Rename log subdirectory | Platform default | string |
| `MOVEMENT_LOG_DIR` | Movement log subdirectory | Platform default | string |

## License

MIT
