# Makefile for Bond broker service

.PHONY: help install lint build clean up down logs

help:
	@echo "Bond Broker service"
	@echo ""
	@echo "Available commands:"
	@echo "  make install  - Install dependencies"
	@echo "  make lint     - Run flake8 and mypy on broker code"
	@echo "  make build    - Build the broker Docker image"
	@echo "  make clean    - Remove Python caches"
	@echo "  make up       - Start Redis + broker with docker compose"
	@echo "  make down     - Stop docker compose services"
	@echo "  make logs     - Tail docker compose logs"

install:
	pip install -r requirements.txt

lint:
	flake8 apps/ engine/
	mypy apps/ engine/

build:
	docker build -f infra/Dockerfile.broker -t bond-broker:latest .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".mypy_cache" -exec rm -rf {} +

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f
