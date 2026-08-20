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
2. Run the container in detached mode with `/data` mounted
3. **Automatically restart the container on system reboot** (`--restart unless-stopped`)

The container will now start automatically when your system boots!

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
```

#### Remove the container:
```bash
docker rm zulip-watcher
```

Or stop and remove in one command:
```bash
docker stop zulip-watcher && docker rm zulip-watcher
```

Or force remove a running container:
```bash
docker rm -f zulip-watcher
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
5. W2MO check-in state (optional) → appends "| Available ✅" when checked in, "Unavailable" when checked out

## W2MO Presence (optional)

When `enable_w2mo_presence = true`, the watcher uses each user's W2MO workday
check-in/out state as the **lowest-priority** signal. It applies when no
calendar event produced a status, and it also **overrides a whole-day working
location** — whole-day locations are usually the Google Workspace default
("In office" every workday), not a deliberate signal, so actual W2MO presence
wins over them. Deliberately set **timed** working locations (e.g. "Home
9:00–13:00"), meetings, lunch and vacations always keep precedence. The user's
custom prefix (text before `|`) is preserved as with every other auto status.

- **Checked in** → "| Available ✅" is appended to the location status, which
  otherwise stays as-is (e.g. `In office | Available ✅` 🏢 or
  `Working remotely | Available ✅` 🏠). The W2MO check-in location replaces a
  whole-day calendar default; a deliberately set timed location is kept.
- **Checked out** → `Unavailable`, but only during Lviv core hours
  (`w2mo_core_hours_start`–`w2mo_core_hours_end`, Mon–Fri). Outside that window
  the auto status is simply cleared to avoid evening/weekend noise.
- **Unknown / no record / no W2MO account** → no signal (auto status cleared).

Configure in `zulip.properties`:

```properties
enable_w2mo_presence = true
w2mo_server_url = https://your-w2mo-server.com
w2mo_auth_token = <long-lived W2MO service JWT>
w2mo_warehouse_id = <warehouse id>
w2mo_core_hours_start = 10
w2mo_core_hours_end = 16
```

The watcher reads presence via `GET /api/workday/currentWorkDayRecordedByEmail`,
which returns any authenticated user's workday record, so a single service token
suffices for the whole team. Google workspace emails (`@lvairo.com`) are mapped
to W2MO accounts by retrying the same local part on `@logivations.com` /
`@pixel-robotics.eu`; resolved mappings are cached.

## Troubleshooting

- **Authentication errors**: Ensure your `zulip.properties` credentials are correct
- **Google API errors**: Verify `credentials.json` is valid and you've completed OAuth flow
- **Network issues**: Check connectivity to both Zulip server and Google APIs
- **Logs**: Check `/data/logs/zulip_status_controller.log` for detailed error messages
- **Docker issues**: Ensure volumes are properly mounted and paths are correct