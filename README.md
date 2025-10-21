# Zulip Status Watcher

The Zulip Status Watcher automatically monitors your Google Calendar and updates your Zulip status based on:
- Current meetings
- Working location (office/home)
- Vacation and out-of-office events
- Lunch breaks

## Setup

### 1. Configuration

Configure Zulip and Google API credentials in `/data/appconfig_static/zulip/zulip.properties`:

```properties
[zulip_status_watcher]
# Zulip configuration
zulip_user_api_token = YOUR_ZULIP_API_TOKEN
zulip_server_url = https://your-zulip-server.com
zulip_user_email = your.email@example.com

# Google Calendar configuration
# Guide: https://www.youtube.com/watch?v=B2E82UPUnOY&t=758s
google_creds = "/data/credentials.json"
google_token_file = "/data/token.json"
```

### 2. Google Calendar Setup

1. Follow [this guide](https://www.youtube.com/watch?v=B2E82UPUnOY&t=758s) to create Google Calendar API credentials
2. Download your `credentials.json` file
3. On first run, you'll need to authenticate via browser to generate `token.json`

## Running the Application

### Option A: Docker (Recommended)

#### Prerequisites:
Ensure your configuration and credentials are in place:
- `/data/appconfig_static/zulip/zulip.properties` - Zulip and Google API configuration
- Paths in `zulip.properties` should point to files within `/data/` (e.g., `/data/credentials.json`, `/data/token.json`)

#### Quick Start:
Simply run the provided script:
```bash
./run_docker.sh
```

This script will:
1. Build the Docker image
2. Run the container with `/data` mounted
3. Automatically remove the container when stopped (`--rm` flag)

#### Manual Docker Commands:

Build the Docker image:
```bash
docker build -t zulip-status-watcher .
```

Run the container in detached mode:
```bash
docker run -d \
  --name zulip-watcher \
  -v /data:/data \
  zulip-status-watcher
```

This mounts the entire `/data` directory, giving the container access to:
- Configuration: `/data/appconfig_static/zulip/zulip.properties`
- Logs: `/data/logs/`
- Google credentials: wherever you specified in `zulip.properties`

#### First-time setup (Google OAuth):
For the initial Google authentication, run interactively:
```bash
docker run -it \
  --name zulip-watcher \
  -v /data:/data \
  zulip-status-watcher
```

Follow the authentication link in the output, authorize the application, and the token will be saved. Then you can run in detached mode.

#### View logs:
```bash
docker logs -f zulip-watcher
```

#### Stop the container:
```bash
docker stop zulip-watcher
docker rm zulip-watcher
```

#### Restart after stopping:
```bash
docker start zulip-watcher
```

### Option B: Manual Python Execution

#### Install dependencies:
```bash
pip install -r requirements.txt
```

#### Run the watcher:
```bash
python3 -m watcher.watcher
```

On first launch, follow the authentication link in the logs, copy your token, and paste it into the terminal. The token will be cached for subsequent runs.

## How It Works

The watcher runs every minute and:
1. Checks your Google Calendar for current events
2. Determines your working location
3. Checks for vacation/out-of-office events
4. Updates your Zulip status accordingly

### Status Priority (highest to lowest):
1. Vacation/Out of Office
2. Current Meeting
3. Lunch Break
4. Working Location (Office/Remote)

## Troubleshooting

- **Authentication errors**: Ensure your `zulip.properties` credentials are correct
- **Google API errors**: Verify `credentials.json` is valid and you've completed OAuth flow
- **Network issues**: Check connectivity to both Zulip server and Google APIs
- **Logs**: Check `/data/logs/zulip_status_controller.log` for detailed error messages
- **Docker issues**: Ensure volumes are properly mounted and paths are correct