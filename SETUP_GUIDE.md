# Trading Agent Setup Guide

Complete guide to implementing the trading agent for daily use with n8n Cloud and Alpaca.

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        YOUR WORKFLOW                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   6:00 AM    n8n Cloud runs daily analysis                          │
│      │                                                               │
│      ▼                                                               │
│   6:05 AM    Python agent screens 30 stocks                         │
│      │       • Fetches price history (MACD, RSI, MA, VWAP)          │
│      │       • Generates signals                                     │
│      │       • Calculates position sizes                             │
│      │                                                               │
│      ▼                                                               │
│   6:10 AM    Results → Email/SMS to you                             │
│      │       "2 BUY signals found: TOST, VRT"                       │
│      │                                                               │
│      ▼                                                               │
│   You click  [Approve] or [Reject] in email                         │
│      │                                                               │
│      ▼                                                               │
│   9:30 AM    If approved → Orders submitted to Alpaca               │
│              (bracket orders with stop-losses)                       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- [ ] n8n Cloud account ($20/month) - https://n8n.io/cloud
- [ ] Alpaca account (free) - https://alpaca.markets
- [ ] Server to run Python (see options below)

---

## Step 1: Set Up Alpaca (10 minutes)

### 1.1 Create Account

1. Go to https://alpaca.markets
2. Sign up for free account
3. Complete identity verification (required for live trading later)

### 1.2 Get API Keys

1. Log into Alpaca dashboard
2. Go to **Paper Trading** section (we'll start here)
3. Click **View** under API Keys
4. Generate new key pair
5. **Save both keys** - you'll need them:
   - `ALPACA_API_KEY`: PKXXXXXXXXXXXX
   - `ALPACA_SECRET_KEY`: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

### 1.3 Test Connection

```bash
# Set environment variables
export ALPACA_API_KEY="your_api_key"
export ALPACA_SECRET_KEY="your_secret_key"
export ALPACA_PAPER="true"

# Test
cd trading_agent
python execution/alpaca_broker.py
```

Expected output:
```
Alpaca Broker Test
==================================================
Mode: paper
Account Info:
  Portfolio Value: $100,000.00
  Cash: $100,000.00
  Buying Power: $100,000.00

Positions:
  No open positions

Market Open: False
```

---

## Step 2: Set Up Python Environment (15 minutes)

You need a server to run the Python agent. Options:

| Option | Cost | Setup Time | Recommended For |
|--------|------|------------|-----------------|
| **Your Mac/PC** | Free | 5 min | Testing only |
| **DigitalOcean Droplet** | $6/mo | 15 min | Production ✓ |
| **AWS EC2 t3.micro** | ~$8/mo | 20 min | Production |
| **Railway.app** | $5/mo | 10 min | Easy setup ✓ |

### Option A: DigitalOcean (Recommended)

1. Create account at https://digitalocean.com
2. Create Droplet:
   - Image: Ubuntu 22.04
   - Plan: Basic $6/mo (1GB RAM)
   - Datacenter: Choose closest
   - Authentication: SSH key (recommended) or password

3. SSH into droplet:
```bash
ssh root@your_droplet_ip
```

4. Install Python and dependencies:
```bash
# Update system
apt update && apt upgrade -y

# Install Python
apt install python3 python3-pip python3-venv -y

# Create app directory
mkdir -p /opt/trading_agent
cd /opt/trading_agent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install requests pandas numpy
```

5. Upload trading agent code:
```bash
# From your local machine
scp -r trading_agent/* root@your_droplet_ip:/opt/trading_agent/
```

6. Set environment variables:
```bash
# Create env file
cat > /opt/trading_agent/.env << EOF
ALPACA_API_KEY=your_api_key
ALPACA_SECRET_KEY=your_secret_key
ALPACA_PAPER=true
TRADING_AGENT_DATA_DIR=/opt/trading_agent/data
EOF

# Add to bashrc
echo "source /opt/trading_agent/.env" >> ~/.bashrc
```

7. Test:
```bash
cd /opt/trading_agent
source venv/bin/activate
python run_daily_analysis.py --mode quick
```

### Option B: Railway.app (Easiest)

1. Create account at https://railway.app
2. Connect GitHub repo with trading agent code
3. Add environment variables in Railway dashboard
4. Railway will auto-deploy

---

## Step 3: Set Up n8n Cloud (15 minutes)

### 3.1 Create Account

1. Go to https://n8n.io/cloud
2. Sign up for trial (or $20/mo plan)
3. Your instance will be at: `your-name.app.n8n.cloud`

### 3.2 Import Workflow

1. In n8n, go to **Workflows** → **Import from File**
2. Upload `workflows/n8n_trading_workflow.json`
3. The workflow will appear with all nodes

### 3.3 Configure Nodes

**Update these nodes:**

1. **"Run Python Analysis" node**:
   - Change path to your server
   - If using SSH: `ssh root@your_server_ip "cd /opt/trading_agent && source venv/bin/activate && python run_daily_analysis.py --output /tmp/result.json && cat /tmp/result.json"`

2. **"Send Email Alert" node**:
   - Click node → Credentials → Create new
   - Set up Gmail or SMTP credentials
   - Update `fromEmail` and `toEmail`

3. **"Approval Webhook" node**:
   - Copy the webhook URL (shown when you click the node)
   - Update the email template with this URL

4. **"Execute Trades" node**:
   - Update path same as analysis node

### 3.4 Set Schedule

The default schedule is 9:35 AM ET (5 minutes after market open).

To change:
1. Click "Daily 9:35 AM Trigger" node
2. Adjust hour/minute
3. Note: n8n uses UTC, so convert your timezone

### 3.5 Activate Workflow

1. Toggle the workflow **Active** in top right
2. Workflow will now run on schedule

---

## Step 4: Add Your Research Scores (5 minutes)

The agent uses your Stage 1D research scores for position sizing and conviction.

Create file `/opt/trading_agent/data/research_scores.json`:

```json
{
  "TOST": {
    "overall_score": 4.40,
    "conviction_tier": "HIGH",
    "thesis": "Vertical SaaS compounder in restaurant tech",
    "bear_case_price": 34,
    "base_case_price": 58,
    "bull_case_price": 73,
    "key_risks": ["Restaurant cyclicality", "Competition from Square"],
    "catalysts": ["International expansion", "Fintech attach rate"]
  },
  "VRT": {
    "overall_score": 4.30,
    "conviction_tier": "HIGH",
    "thesis": "Data center infrastructure beneficiary",
    "bear_case_price": 84,
    "base_case_price": 145,
    "bull_case_price": 180,
    "key_risks": ["Tariff exposure", "Capex cyclicality"],
    "catalysts": ["AI buildout", "Power constraints driving demand"]
  }
}
```

---

## Step 5: Test End-to-End (10 minutes)

### 5.1 Manual Test

```bash
# On your server
cd /opt/trading_agent
source venv/bin/activate

# Run quick analysis
python run_daily_analysis.py --mode quick --output /tmp/test.json

# Check output
cat /tmp/test.json | python -m json.tool
```

### 5.2 Test n8n Workflow

1. In n8n, click **Execute Workflow** (play button)
2. Watch each node execute
3. Check that email arrives
4. Click approval link to test webhook

### 5.3 Test Alpaca Paper Trading

```bash
python -c "
from execution.alpaca_broker import AlpacaBroker

broker = AlpacaBroker()

# Place test order
result = broker.place_order(
    symbol='AAPL',
    qty=1,
    side='buy',
    order_type='limit',
    limit_price=150.00,
    stop_loss=140.00
)

print(result)

# Cancel it
if 'id' in result:
    broker.cancel_order(result['id'])
    print('Order cancelled')
"
```

---

## Step 6: Go Live Checklist

After 2-4 weeks of paper trading:

- [ ] Review paper trading results
- [ ] Tune any thresholds based on results
- [ ] Fund Alpaca account (min $100 recommended to start)
- [ ] Get live API keys from Alpaca
- [ ] Update environment variables: `ALPACA_PAPER=false`
- [ ] Start with small position sizes (reduce by 50%)
- [ ] Monitor closely for first week

---

## Daily Workflow

Once set up, your daily workflow is:

| Time | What Happens |
|------|--------------|
| 6:00 AM | n8n triggers analysis |
| 6:05 AM | You receive email with opportunities |
| 6:05-9:30 AM | Review opportunities, click Approve/Reject |
| 9:30 AM | If approved, orders submitted at market open |
| 9:35 AM | Confirmation email with order status |

---

## Troubleshooting

### "No module named 'requests'"
```bash
pip install requests
```

### "Alpaca API credentials required"
```bash
export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"
```

### n8n can't connect to server
1. Check server is running
2. Check firewall allows connection
3. Try using n8n's HTTP Request node instead of Execute Command

### No opportunities found
- Check `data/screening/persistent_performers.json` for tracked stocks
- Run with `--symbols TOST,VRT` to test specific stocks
- Check logs for errors

---

## Files Reference

```
trading_agent/
├── run_daily_analysis.py      # Main entry point (n8n calls this)
├── decision_framework/        # Core analysis engine
├── execution/
│   └── alpaca_broker.py       # Alpaca integration
├── screening/
│   └── universe_screener.py   # Stock discovery
├── workflows/
│   └── n8n_trading_workflow.json  # Import into n8n
├── data/                      # Created at runtime
│   ├── research_scores.json   # Your Stage 1D scores
│   ├── screening/             # Performer tracking
│   └── decisions/             # Decision history
└── SETUP_GUIDE.md             # This file
```

---

## Support

- Alpaca Docs: https://docs.alpaca.markets
- n8n Docs: https://docs.n8n.io
- This agent's code: Review the source files for detailed comments

---

## Next Steps (Future Enhancements)

1. **Add SMS alerts**: Use Twilio node in n8n
2. **Dashboard**: Build simple web UI to view positions
3. **Multiple strategies**: Add momentum-only or value-only modes
4. **Auto-rebalancing**: Monthly position rebalancing
5. **Full automation**: Remove confirmation step once confident
