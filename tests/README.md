# Test Suite: WinsPool

## Overview
This directory contains the automated test suite for the WinsPool application. The suite utilizes the pytest framework to ensure system reliability, prevent regression, and validate core business logic.

## Strategy and Approach
Testing follows the Arrange-Act-Assert (AAA) pattern to maintain clarity and isolation.
- Unit Tests: Validate individual functions and services.
- Integration Tests: Validate FastAPI endpoints and request/response lifecycles.
- Mocking: External dependencies (Firestore, Google Gemini API) are mocked to ensure tests are deterministic and do not incur costs or latency.

## Environment Setup
1. Ensure the project dependencies are installed:
   pip install -r requirements.txt
2. Tests require no live API credentials, as all external services are mocked in conftest.py.

## Execution
Run all tests from the project root:
pytest

Run with verbose output:
pytest -v

Generate a coverage report:
pytest --cov=services --cov=routes

## Structure
- conftest.py: Contains global fixtures and shared mocks.
- test_api.py: Validates API endpoints and routing.
- test_services.py: Validates backend logic and data processing.

## Contribution Standards
- All new features must be accompanied by relevant test cases.
- Use fixtures for setup/teardown logic instead of hardcoded values.
- Mocks for any new external integrations must be added to conftest.py. 