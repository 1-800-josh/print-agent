# Print Agent

Background service for automatic print task synchronization with hot folder support.

## Overview

The Print Agent is a Python service that:
- Syncs READY_FOR_PRODUCTION orders from the API to network paths
- Organizes files by material and delivery date
- Uses Morse code encoding for filenames
- Watches hot folders for file operations to update task status
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

Create a `.env` file:

```env
API_BASE_URL=http://localhost:3000
API_KEY=your-api-key
ORGANISATION_ID=your-org-id
UPLOADTHING_APP_ID=your-uploadthing-app-id
SYNC_INTERVAL_SECONDS=60
MAX_WORKERS=10
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

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_BASE_URL` | Internal portal API URL | `http://localhost:3000` |
| `API_KEY` | Service API key | Required |
| `ORGANISATION_ID` | Organisation UUID | Required |
| `UPLOADTHING_APP_ID` | UploadThing app ID | Required |
| `SYNC_INTERVAL_SECONDS` | Seconds between syncs | `60` |
| `MAX_WORKERS` | Download worker processes | `10` |
| `OUTPUT_BASE_DIR` | Local output directory | Platform default |
| `LOG_DIR` | Log directory | Platform default |

## License

MIT
