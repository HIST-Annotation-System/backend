# Backend API (FastAPI)

Backend project for the system built on the **FastAPI** framework. Dependency and environment management is handled by **uv** (inside Docker).

## Directory Structure

The main application code is located in the `app/` folder:

- `core/` - Main application configuration (e.g., Pydantic settings, CORS).
- `dependencies/` - Dependencies (Dependency Injection) shared across different endpoints.
- `exceptions/` - Custom exceptions and global error handlers.
- `models/` - Database models (e.g., SQLAlchemy, SQLModel).
- `repositories/` - Data Access Layer (DAL) abstraction.
- `routers/` - API endpoint definitions grouped functionally.
- `schemas/` - Pydantic models (DTOs) used for input and output data validation.
- `services/` - Business logic of the application, separated from routers and database queries.

## Requirements

- [Docker](https://www.docker.com/) and Docker Compose

## Installation and Local Setup

1. **Clone the repository** and navigate to the backend folder.
2. **Create a local environment file:**
   Copy `.env.example` to `.env` and adjust the variables if necessary. 
3. **Run the environment using Docker Compose:**
   ```bash
   docker compose up -d --build
   ```
   This will start both the FastAPI server and the MinIO object storage.
   - API Server will be available at: `http://localhost:8000`
   - MinIO API will be available at: `http://localhost:9000`
   - MinIO Web UI will be available at: `http://localhost:9001`

4. **Stop the environment:**
   ```bash
   docker compose down
   ```

## API Documentation

Once the server is running, the interactive Swagger UI documentation is available at:
- [http://localhost:8000/docs](http://localhost:8000/docs)

Alternative documentation (ReDoc):
- [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Environment Variables

Application configuration is loaded using `pydantic-settings`. 
Loading priorities:
1. Environment variables passed via `docker-compose.yml` or system terminal
2. `.env` file

To add new variables, add them to the `Settings` class in `app/core/config.py`, and then set their values in your `.env` file.
