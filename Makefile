# Makefile for bond project

.PHONY: help install test lint deploy clean

help:
	@echo "Bond - Real-time market data streaming platform"
	@echo ""
	@echo "Available commands:"
	@echo "  make install   - Install dependencies"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run linters (flake8 + mypy)"
	@echo "  make deploy    - Build and push Docker images"
	@echo "  make clean     - Clean up temporary files"
	@echo "  make up        - Start services with Docker Compose"
	@echo "  make down      - Stop services"
	@echo "  make logs      - View service logs"

install:
	pip install -r requirements.txt

test:
	pytest -v tests/

lint:
	flake8 apps/ connectors/ engine/ sdk/
	mypy apps/ connectors/ engine/ sdk/

deploy:
	docker build -f infra/Dockerfile.api -t bond-api:latest .
	docker build -f infra/Dockerfile.broker -t bond-broker:latest .
	@echo "Images built. Push with: docker push <registry>/bond-api:latest"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +

up:
	cd infra && docker compose up -d

down:
	cd infra && docker compose down

logs:
	cd infra && docker compose logs -f
