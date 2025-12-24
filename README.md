# Rent Radar - Real Estate Monitoring & Alert System

Automated property tracking system that monitors MercadoLibre real estate listings in real-time, detects new opportunities, and sends instant notifications via Telegram.

## 📑 Índice

- [🎯 Overview](#-overview)
- [✨ Key Features](#-key-features)
- [🛠️ Tech Stack](#-tech-stack)
- [📋 Use Case](#-use-case)

---

## 🎯 Overview

MeliCasa scrapes rental property listings from MercadoLibre (Argentina's largest marketplace), stores historical data, and alerts you immediately when new properties matching your criteria appear. Perfect for competitive rental markets where being first matters.

## ✨ Key Features

- 🔍 **Automated Web Scraping** - Monitors specific neighborhoods with custom filters (location, price, surface area, parking)
- 💾 **Persistent Storage** - MongoDB database for historical analysis and trend tracking
- 📊 **Interactive Dashboard** - Real-time visualizations of property listings and market trends
- 🤖 **Airflow Orchestration** - Scheduled scraping jobs with configurable intervals
- 📱 **Telegram Notifications** - Instant alerts for new listings and price changes
- 💱 **Currency Conversion** - Automatic ARS/USD conversion with validation
- 🏗️ **Modular Architecture** - FastAPI backend + React frontend + Nginx reverse proxy
- 🐳 **Fully Dockerized** - One-command deployment with docker-compose

## 🛠️ Tech Stack

- **Backend:** Python, FastAPI, BeautifulSoup4, Requests
- **Database:** MongoDB Atlas
- **Orchestration:** Apache Airflow 2.8.1
- **Frontend:** React.js
- **Web Server:** Nginx
- **Notifications:** Telegram Bot API
- **Infrastructure:** Docker, Docker Compose

## 📋 Use Case

Ideal for apartment hunters in Greater Buenos Aires who want to be the first to know about new listings matching their exact requirements, without manually checking MercadoLibre multiple times per day.
