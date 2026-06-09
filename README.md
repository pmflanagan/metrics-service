# Metrics service

A modern Django-based service built for the Ansible Automation Platform (AAP) ecosystem, featuring comprehensive task management, REST APIs, and automated background job processing.

## Features

- **🚀 Modern Django Architecture** - Django 5.2+ with clean app-based structure
- **📊 Automated Task Management** - Feature-enable controlled task groups with automatic routing
- **⚡ Smart Task Routing** - Automatic submission to dispatcherd with no manual intervention
- **🔌 REST API** - Versioned RESTful APIs with OpenAPI documentation
- **🔐 Authentication & Authorization** - Django-Ansible-Base integration with RBAC
- **📈 Real-time Dashboard** - Web-based task monitoring and management interface
- **🐳 Docker Ready** - Multi-container deployment with PostgreSQL
- **🧪 Comprehensive Testing** - Unit and integration tests with coverage reporting
- **📝 API Documentation** - Interactive Swagger/OpenAPI documentation
- **🔧 Metrics Collection** - Integrated metrics-utility for data collection

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd metrics-service

# Start all services
docker-compose up -d

# Create a superuser (optional)
docker-compose exec metrics-service python manage.py createsuperuser
```

Your service will be available at:

- **Application**: http://localhost:8000
- **API Documentation**: http://localhost:8000/api/docs/
- **Admin Interface**: http://localhost:8000/admin/
- **Task Dashboard**: http://localhost:8000/dashboard/

### Option 2: Local Development

```bash
# Prerequisites: Python 3.12, PostgreSQL 13+

# Install dependencies (project uses uv)
uv sync --dev

# Configure (optional — for local overrides)
cp settings.local.py.example settings.local.py
# Edit settings.local.py to configure your local development environment.

# Set up database (configure via environment variables if needed)
# See Configuration section below for environment variable options
python manage.py migrate
python manage.py metrics_service init-default-settings
python manage.py metrics_service init-service-id
python manage.py metrics_service init-system-tasks
python manage.py createsuperuser

# Start complete service (Django + dispatcher + scheduler)
python manage.py metrics_service run
```

### Option 3: Local development, with uv and metrics-utility from sources

Edit `pyproject.toml` such that:

```diff
 [tool.uv.sources]
 django-ansible-base = { git = "https://github.com/ansible/django-ansible-base", rev = "devel" }
+metrics-utility = { path = "../metrics-utility", editable = true }
```

```
uv sync
uv run ./manage.py migrate
uv run ./manage.py createsuperuser
uv run ./manage.py metrics_service run
uv run ./scripts/run_task.py hello_world # debugging individual tasks
```

### Endpoints

```bash
# List all tasks
GET /api/v1/tasks/

# Create a new task
POST /api/v1/tasks/
{
  "name": "Hello World Task",
  "function_name": "hello_world",
  "task_data": {}
}

# Get running tasks
GET /api/v1/tasks/running/

# Retry a failed task
POST /api/v1/tasks/{id}/retry/

# Available task functions
GET /api/v1/tasks/available_functions/
```

### Built-in Task Functions

**System Tasks** (always enabled):

- `cleanup_old_tasks` - Clean up completed/failed tasks
- `hello_world` - Simple test task for dispatcherd integration
- `execute_db_task` - Execute database-defined tasks with lifecycle management

**Metrics Collection Tasks** (controlled by `METRICS_COLLECTION`, default: enabled):

- `collect_hourly_metrics` - Collect time-series metrics every hour (collector type via `collector_type` parameter)
- `collect_snapshot_metrics` - Collect daily snapshot metrics (collector type via `collector_type` parameter)
- `daily_metrics_rollup` - Merge hourly collections and create daily rollup summary
- `cleanup_metrics_data` - Clean up old metrics data based on retention policies

**Anonymization and Transmission Tasks** (controlled by `ANONYMIZED_DATA_COLLECTION`, default: enabled, customer opt-out):

- `daily_anonymize_and_prepare` - Anonymize daily rollup and prepare for transmission
- `send_anonymized_to_segment` - Send anonymized metrics to Segment.com

## Background Tasks

The service includes an automated background task system with intelligent routing:

### Unified Service Management

```bash
# Start complete service (init*, then Django + dispatcher + scheduler)
python manage.py metrics_service run

# Start with custom configuration
python manage.py metrics_service run --workers 4

# Individual components
python manage.py runserver 0.0.0.0:8000  # web
python manage.py run_dispatcherd --workers 2  # worker
python manage.py run_task_scheduler  # scheduler
```

### Automatic Task Routing

Tasks are automatically routed based on their properties:

- **Immediate tasks** → Direct to dispatcherd
- **Scheduled tasks** → APScheduler with DateTrigger
- **Recurring tasks** → APScheduler with CronTrigger

No manual intervention required - create a task and it's automatically processed!

### Task Groups & Feature Flags

We have these feature flags:

|flag|default|
|-|-|
|`METRICS_COLLECTION`|true|
|`ANONYMIZED_DATA_COLLECTION`|true|
|`DASHBOARD_COLLECTION`|false (customer opt-in)|

You can change defaults using `METRICS_SERVICE_FEATURE_ENABLED__` prefixed environment variables.

```sh
# Pause local collectors, rollup, and metrics cleanup (default: true)
METRICS_SERVICE_FEATURE_ENABLED__METRICS_COLLECTION=false

# Disable anonymization and Segment transmission (default: true)
METRICS_SERVICE_FEATURE_ENABLED__ANONYMIZED_DATA_COLLECTION=false
```

These environment variables (or their default values) are used to populate the feature flags database tables during `manage.py metrics_service init-default-settings`. You can also use `python manage.py metrics_service remove-default-settings` to remove these settings from the database.

The feature flag values in the database determine which scheduled tasks run (checked at execution time). If a value is missing from the database, the environment variable or static default is used as the fallback.

After upgrading to a version that adds `METRICS_COLLECTION`, run `python manage.py metrics_service init-system-tasks` once so system tasks include `_feature_flag` for metrics collection templates.


## Development

### Code Quality Tools

```bash
# Format + lint + test in one step (via poe task runner)
uv run poe check

# Or individually
uv run poe format     # ruff format (includes import sorting)
uv run poe lint       # ruff check
uv run poe unit-test  # pytest

# Direct ruff commands
ruff format .
ruff check . --fix

# Type checking (optional, gradual adoption)
mypy .
```

### Pre-commit Hooks

This project uses pre-commit hooks to ensure code quality and automatically sync requirements files:

```bash
# Install pre-commit hooks
pre-commit install

# Run hooks on all files
pre-commit run --all-files

# Run hooks manually
pre-commit run
```

The pre-commit configuration automatically runs:

- `ruff check --fix` — lint and auto-fix
- `ruff-format` — code formatting
- Platform Service Framework validation

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=apps --cov=metrics_service --cov-report=html

# Run specific test categories
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests only
```

### Database Operations

```bash
# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Initialize settings table with feature flag defaults
python manage.py metrics_service init-default-settings

# Remove feature flags from settings
python manage.py metrics_service remove-default-settings

# Initialize DAB ServiceID (required after first migration)
python manage.py metrics_service init-service-id

# Initialize system tasks
python manage.py metrics_service init-system-tasks
```

## Configuration

Metrics Service uses [Dynaconf](https://www.dynaconf.com/) for settings management, following the [Platform Service Framework](https://github.com/ansible/platform-service-framework).

### Quick Start

**Development Mode** (default):

> [!IMPORTANT]
> The following example assumes those values exported as environment variables,
> to set on the settings.local.py file remove the `METRICS_SERVICE_` prefix.

```bash
# Project
DJANGO_SETTINGS_MODULE=metrics_service.settings
METRICS_SERVICE_MODE=development
METRICS_SERVICE_SECRET_KEY=dev-secret-key-change-in-production
METRICS_SERVICE_DEBUG="true"
METRICS_SERVICE_ALLOWED_HOSTS='["localhost","127.0.0.1","metrics-service","0.0.0.0"]'

# Database
METRICS_SERVICE_DATABASES__default__ENGINE=django.db.backends.postgresql
METRICS_SERVICE_DATABASES__default__HOST=postgres
METRICS_SERVICE_DATABASES__default__PORT=5432
METRICS_SERVICE_DATABASES__default__USER=metrics_service
METRICS_SERVICE_DATABASES__default__PASSWORD=metrics_service
METRICS_SERVICE_DATABASES__default__NAME=metrics_service
METRICS_SERVICE_DATABASES__default__OPTIONS__sslmode=prefer

# Task App
METRICS_SERVICE_FEATURE_ENABLED__ANONYMIZED_DATA_COLLECTION="true"
DISPATCHERD_CONFIG_FILE=/app/apps/settings/dispatcherd.yaml
DISPATCHERD_ENABLED="true"
```

```bash
python manage.py runserver
```

**Production Mode**:

```bash
# Set environment mode and required secrets
export METRICS_SERVICE_MODE=production
export METRICS_SERVICE_SECRET_KEY="your-secure-random-key"
export METRICS_SERVICE_ALLOWED_HOSTS="yourdomain.com,api.yourdomain.com"

# Override defaults as needed
export METRICS_SERVICE_DATABASES__default__HOST=prod-db.example.com
export METRICS_SERVICE_DATABASES__default__PASSWORD=secure-password

python manage.py runserver
```

### Configuration Methods

Settings are loaded in order of precedence (lowest to highest):

Read Only (overridable)

- `metrics_service/settings.py` - Framework defaults

Editable:

- `apps/settings/defaults.py` - Defaults for the whole project
- `apps/core/settings.py` - Core settings, DAB related settings
- `apps/*/settings.py` - Each app settings in the loading order
- `apps/settings/{mode}.py` - Settings specific to the current `METRICS_SERVICE_MODE`
- `settings.local.py` - For local settings (git ignored)
- `/etc/ansible-automation-platform/metrics_service/` - for prod environment overrides
- `METRICS_SERVICE_` prefixed environment variables

### Common Environment Variables

| Variable                                       | Description                               | Required in Production       |
| ---------------------------------------------- | ----------------------------------------- | ---------------------------- |
| `METRICS_SERVICE_MODE`                         | Environment mode (development/production) | No (defaults to development) |
| `METRICS_SERVICE_SECRET_KEY`                   | Django secret key                         | **Yes**                      |
| `METRICS_SERVICE_DEBUG`                        | Enable debug mode                         | No                           |
| `METRICS_SERVICE_LOG_LEVEL`                    | Logging level (DEBUG/INFO/WARNING/ERROR)  | No (defaults to INFO)        |
| `METRICS_SERVICE_DATABASES__default__HOST`     | Database host                             | No (has default)             |
| `METRICS_SERVICE_DATABASES__default__PASSWORD` | Database password                         | No (has default)             |
| `METRICS_SERVICE_ALLOWED_HOSTS`                | Allowed hosts (comma-separated)           | **Yes** (production)         |

**Note:** Use double underscores (`__`) for nested settings:

```bash
# Nested database configuration
export METRICS_SERVICE_DATABASES__default__HOST=localhost
export METRICS_SERVICE_DATABASES__default__PORT=5432
```

### Logging Configuration

Metrics Service uses a centralized logging system that integrates with Django's logging framework. All log levels are controlled by a single environment variable.

**Setting Log Level:**

```bash
# For development - see all debug messages
export METRICS_SERVICE_LOG_LEVEL=DEBUG

# For production - informational messages only
export METRICS_SERVICE_LOG_LEVEL=INFO

# For troubleshooting - warnings and errors
export METRICS_SERVICE_LOG_LEVEL=WARNING

# For critical issues only
export METRICS_SERVICE_LOG_LEVEL=ERROR
```

**Quick Debug Mode:**

```bash
# Run with debug logging temporarily
METRICS_SERVICE_LOG_LEVEL=DEBUG python manage.py runserver

# Or for the complete service
METRICS_SERVICE_LOG_LEVEL=DEBUG python manage.py metrics_service run
```

**Log Output Format:**

All logs use Django's configured format with timestamps, log levels, request IDs (when applicable), module names, and messages:

```
2025-01-18 10:15:23,456 INFO     [abc123] apps.tasks.signals New task created: Cleanup (ID: 42)
2025-01-18 10:15:24,789 WARNING  [] apps.core.utils Database connection slow: 2.3s
```

To inspect the full settings loading history or debug a specific variable:

```bash
export DJANGO_SETTINGS_MODULE=metrics_service.settings
uv run dynaconf inspect -m debug -f yaml   # full loading history
uv run dynaconf inspect -k VARIABLE_NAME   # single variable
```

## Deployment

### Docker Production

```bash
# Build production image
docker build -t metrics-service .

# Run with production settings
docker run -p 8000:8000 \
  -e METRICS_SERVICE_MODE=production \
  -e METRICS_SERVICE_SECRET_KEY=your-secret-key \
  -e METRICS_SERVICE_DATABASES__default__HOST=your-db-host \
  -e METRICS_SERVICE_DATABASES__default__PASSWORD=your-db-password \
  metrics-service
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes with tests
4. Run the test suite: `uv run pytest`
5. Run code quality checks: `uv run poe check`
6. Submit a pull request

### Development Standards

- **Code Style**: Ruff formatting, 120 character line length
- **Type Hints**: Required for all new code
- **Documentation**: Docstrings for public APIs
- **Testing**: Test coverage for new features
- **Commits**: Conventional commit messages

## License

This project is licensed under the Apache License - see the [LICENSE](LICENSE) file for details.

## Support

- **Documentation**: Check the [CLAUDE.md](CLAUDE.md) file for detailed development guidance
- **Issues**: Report bugs and feature requests via GitHub issues
- **API Documentation**: Interactive docs available at `/api/docs/` when running

