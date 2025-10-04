# Loandisk Dashboard

A comprehensive Streamlit dashboard for analyzing loan disbursements, repayments, and Portfolio at Risk (PAR) calculations.

## Features

- 📊 **Real-time Data Visualization** - Interactive charts and graphs
- 💰 **Disbursement Tracking** - Monitor loan disbursements by branch and time
- 🔄 **Repayment Analysis** - Track repayment patterns and trends
- 📈 **PAR Calculations** - Portfolio at Risk analysis with branch-wise breakdowns
- ⚡ **Performance Optimized** - Caching and lazy loading for fast performance
- 🔐 **Secure** - Uses Streamlit secrets for API keys and credentials

## Deployment

This app is deployed on Streamlit Cloud and uses:

- **Google Sheets** for data storage
- **Loandisk API** for real-time data fetching
- **Streamlit Secrets** for secure credential management

## Performance Features

- 🚀 **Caching System** - 5-30 minute caches for different data types
- 🎛️ **Conditional Loading** - Optional PAR calculation for faster loading
- ⚡ **Pre-computed Visualizations** - Cached chart data for instant rendering
- 🔄 **Parallel Processing** - Multi-threaded API calls for better performance

## Local Development

To run locally:

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Add your credentials to `.streamlit/secrets.toml`:
   ```toml
   [secrets]
   LOANDISK_API_KEY = "your_api_key_here"
   GOOGLE_SHEETS_KEY = "your_sheets_key_here"
   SERVICE_ACCOUNT_JSON = '{"type": "service_account", ...}'
   ```

3. Run the app:
   ```bash
   streamlit run app.py
   ```

## Data Sources

- **Loandisk API** - Real-time loan data
- **Google Sheets** - Cached data storage and historical records

## Security

- All sensitive credentials are stored in Streamlit secrets
- API keys are not exposed in the code
- Google Sheets access uses service account authentication
