# Albert Heijn Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A custom Home Assistant integration for Albert Heijn (Dutch supermarket), providing receipt and savings data as sensors.

## Features

- **Last receipt total** — Amount of your most recent purchase
- **Last receipt discount** — Discount applied on the last receipt
- **Last receipt date** — Timestamp of the most recent receipt
- **Last receipt items** — Full item list of the most recent receipt as text
- **Receipt count** — Total number of receipts in your account
- **Total bonus savings** — Cumulative bonus/discount savings across all receipts

## Installation via HACS

1. In HACS, go to **Integrations** → click the three-dot menu → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Install **Albert Heijn** from HACS
4. Restart Home Assistant

## Manual Installation

Copy the `custom_components/ah_integration` folder to your HA `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings → Devices & Services → Add Integration** and search for **Albert Heijn**
2. Open the login URL shown in your browser and complete the Albert Heijn login
3. Copy the redirect URL (starts with `appie://`) or just the code from it and paste it into the form

Authentication tokens are stored in your HA config entry and auto-refreshed — you only need to log in once.

## Requirements

- Home Assistant 2024.1+
- Python 3.11+
- An Albert Heijn account with the Appie app
