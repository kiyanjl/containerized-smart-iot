# Quick Start Guide

## 30-Second Setup

### 1. Prerequisites

- Docker & Docker Compose installed
- .env file with Telegram token (see DEPLOYMENT_GUIDE.md)

### 2. Start the Stack

**Windows (PowerShell):**

```powershell
cd scripts
.\start_stack.ps1
```

**Linux/macOS:**

```bash
docker compose up -d
```

### 3. Wait for Services (~30 seconds)

Monitor startup:

```bash
docker compose logs -f
```

### 4. Access Dashboard

- **Dashboard**: http://localhost:7501
- **Grafana**: http://localhost:3100
- **InfluxDB**: http://localhost:8086
- **Grafana**: http://localhost:3100
- **InfluxDB**: http://localhost:8086

---

## What You'll See

### On the Dashboard

1. **KPI Cards** - Real-time metrics (assets, telemetry, events)
2. **Warehouse Cards** - Live temperature, humidity, stock for each warehouse
3. **State Timeline** - 6-hour history chart showing state changes
4. **Event Log** - Live feed of all system events
5. **Quick Actions** - Manual control buttons for each warehouse
6. **Alerts Feed** - Critical and overload state notifications

### Events Flow

```
Sensor Simulator publishes telemetry
    ↓
Controller evaluates rules
    ↓
Updates InfluxDB & publishes commands
    ↓
Actuator confirms execution
    ↓
Dashboard shows real-time updates
    ↓
Critical states trigger Telegram alerts
```

---

## Common Tasks

### View Warehouse Status

Dashboard → Warehouse Status cards show:

- Temperature (°C)
- Humidity (%)
- Stock (%)
- Door open/closed
- Last sample time

### Send Manual Command

1. Dashboard → Quick Actions
2. Select warehouse
3. Click action (Fan ON, Emergency Shutdown, etc.)
4. See "Pending Commands" counter
5. Watch for confirmation

### Check System Health

Dashboard → System Health section shows:

- Controller status
- Alert service status
- InfluxDB & Grafana URLs
- Service connectivity

### Monitor Events

Dashboard → Live Event Log shows:

- Event type (SENSOR, COMMAND, CONFIRMATION, OFFLINE, etc.)
- Warehouse affected
- Source (simulator, controller, dashboard)
- Color-coded by type

### Visualize in Grafana

1. Go to http://localhost:3100
2. Select "Warehouse Metrics" dashboard
3. Set time range (top right)
4. Hover for details
5. Annotations show connectivity and actuation events

---

## Verification Checklist

- [ ] Dashboard loads at http://localhost:18501 (or configured port)
- [ ] Warehouse status cards show real data (not zeros)
- [ ] Event log has entries
- [ ] State timeline chart is populated
- [ ] Grafana dashboard has data points
- [ ] Manual commands respond (click button → see status update)
- [ ] Telegram alerts received (simulate: set stock > 90%)

---

## Troubleshooting Quick Fixes

**Dashboard won't load?**

```bash
docker compose logs dashboard | tail -20
```

**No telemetry data?**

```bash
docker compose logs sensor-simulator | grep "Published"
```

**Check controller processing:**

```bash
docker compose logs controller-service | grep "Decision:"
```

**Verify InfluxDB has data:**

```bash
curl http://localhost:8086/api/v2/health
```

---

## Stopping the Stack

```bash
docker compose down
```

Remove volumes too (clean slate):

```bash
docker compose down -v
```

---

## Next Steps

- Read [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for detailed documentation
- Check [README.md](README.md) for system architecture
- Review [interfaces_specification.txt](interfaces_specification.txt) for API details
- Modify rules in [catalog.json](catalog-service/catalog.json)
- Customize dashboard in [dashboard/dashboard.py](dashboard/dashboard.py)

---

**Note**: Dashboard runs on port 18501 by default to avoid Windows port reservation conflicts. You can change this in `.env` using `DASHBOARD_PORT`.

**Everything Ready?** Your smart warehouse is live! 🏭✨
