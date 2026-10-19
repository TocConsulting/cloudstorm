"""
HTML report generation for cloudstorm.
"""
from datetime import datetime, timezone


def generate_html_report(results: dict) -> str:
    """Generate HTML report from results."""
    meta = results.get("meta", {})
    summary = results.get("summary", {})
    times = results.get("response_times", {})
    by_region = results.get("by_region", {})
    config = meta.get("config", {})
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    total_req = summary.get("total_requests", 0)
    success_req = summary.get("successful_requests", 0)
    failed_req = summary.get("failed_requests", 0)
    error_rate = summary.get("error_rate", 0)
    rps = summary.get("requests_per_second", 0)

    min_ms = times.get("min_ms", 0)
    avg_ms = times.get("avg_ms", 0)
    max_ms = times.get("max_ms", 0)
    median_ms = times.get("median_ms", 0)
    p90_ms = times.get("p90_ms", 0)
    p95_ms = times.get("p95_ms", 0)
    p99_ms = times.get("p99_ms", 0)

    # Determine status color
    if error_rate == 0:
        status_class = "status-pass"
        status_text = "PASSED"
    elif error_rate < 0.05:
        status_class = "status-warn"
        status_text = "WARNING"
    else:
        status_class = "status-fail"
        status_text = "FAILED"

    # Build region rows
    region_rows = ""
    region_labels_js = []
    region_reqs_js = []
    region_avg_js = []
    region_p95_js = []
    region_failed_js = []

    for region, data in by_region.items():
        s = data.get("summary", {})
        t = data.get("response_times", {})
        r_total = s.get("total_requests", 0)
        r_success = s.get("successful_requests", 0)
        r_failed = s.get("failed_requests", 0)
        r_avg = t.get("avg_ms", 0)
        r_p95 = t.get("p95_ms", 0)
        r_rps = s.get("requests_per_second", 0)
        r_err = (r_failed / r_total * 100) if r_total > 0 else 0

        # Bar width for visual indicator
        bar_pct = (r_total / total_req * 100) if total_req > 0 else 0

        region_rows += f"""
                    <tr>
                        <td><span class="region-badge">{region}</span></td>
                        <td>{r_total:,}</td>
                        <td class="text-success">{r_success:,}</td>
                        <td class="text-error">{r_failed:,}</td>
                        <td>{r_avg:.2f}</td>
                        <td>{r_p95:.2f}</td>
                        <td>{r_rps:.1f}</td>
                        <td>{r_err:.2f}%</td>
                        <td>
                            <div class="bar-container">
                                <div class="bar-fill" style="width: {bar_pct:.0f}%"></div>
                            </div>
                        </td>
                    </tr>"""

        region_labels_js.append(f'"{region}"')
        region_reqs_js.append(str(r_total))
        region_avg_js.append(f"{r_avg:.2f}")
        region_p95_js.append(f"{r_p95:.2f}")
        region_failed_js.append(str(r_failed))

    # Build endpoint rows for HTML
    endpoints_data = results.get("endpoints", {})
    endpoint_rows = ""
    for ep_name in sorted(endpoints_data.keys()):
        ep = endpoints_data[ep_name]
        ep_reqs = ep.get("requests", 0)
        ep_ok = ep.get("successful", 0)
        ep_fail = ep.get("failed", 0)
        ep_avg = ep.get("avg_ms", 0)
        ep_min = ep.get("min_ms", 0)
        ep_max = ep.get("max_ms", 0)
        ep_err = (ep_fail / ep_reqs * 100) if ep_reqs > 0 else 0
        endpoint_rows += f"""
                    <tr>
                        <td><span class="region-badge">{ep_name}</span></td>
                        <td>{ep_reqs:,}</td>
                        <td class="text-success">{ep_ok:,}</td>
                        <td class="text-error">{ep_fail:,}</td>
                        <td>{ep_avg:.0f}</td>
                        <td>{ep_min:.0f}</td>
                        <td>{ep_max:.0f}</td>
                        <td>{ep_err:.2f}%</td>
                    </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>cloudstorm Report | {meta.get('target_url', '')}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <script>if(typeof Chart==='undefined'){{document.querySelectorAll('.chart-container').forEach(function(el){{el.innerHTML='<p style="color:var(--text-muted);text-align:center;padding:40px">Charts require internet (Chart.js CDN). Data is in the tables below.</p>'}})}}</script>
    <style>
        :root {{
            --bg-primary: #0a0e1a;
            --bg-secondary: #111827;
            --bg-card: #1a2234;
            --bg-card-hover: #1f2937;
            --border: #2a3548;
            --text-primary: #e5e7eb;
            --text-secondary: #9ca3af;
            --text-muted: #6b7280;
            --accent-blue: #3b82f6;
            --accent-blue-glow: rgba(59, 130, 246, 0.3);
            --accent-orange: #f59e0b;
            --accent-orange-glow: rgba(245, 158, 11, 0.3);
            --accent-cyan: #06b6d4;
            --success: #10b981;
            --success-bg: rgba(16, 185, 129, 0.1);
            --error: #ef4444;
            --error-bg: rgba(239, 68, 68, 0.1);
            --warning: #f59e0b;
            --warning-bg: rgba(245, 158, 11, 0.1);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 24px;
        }}

        /* Header */
        .header {{
            background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            border-bottom: 1px solid var(--border);
            padding: 32px 0;
            position: relative;
            overflow: hidden;
        }}

        .header::before {{
            content: '';
            position: absolute;
            top: -50%;
            left: -10%;
            width: 120%;
            height: 200%;
            background: radial-gradient(ellipse at 30% 50%, var(--accent-blue-glow) 0%, transparent 60%),
                        radial-gradient(ellipse at 70% 50%, var(--accent-orange-glow) 0%, transparent 60%);
            opacity: 0.4;
            pointer-events: none;
        }}

        .header-content {{
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 20px;
        }}

        .logo-section {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}

        .logo-icon {{
            width: 48px;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .logo-icon svg {{
            width: 48px;
            height: 48px;
        }}

        .logo-text {{
            font-size: 1.8em;
            font-weight: 800;
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, var(--accent-blue) 0%, var(--accent-cyan) 50%, var(--accent-orange) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .logo-subtitle {{
            font-size: 0.85em;
            color: var(--text-secondary);
            font-weight: 400;
        }}

        .status-badge {{
            padding: 8px 20px;
            border-radius: 50px;
            font-weight: 700;
            font-size: 0.85em;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }}

        .status-pass {{ background: var(--success-bg); color: var(--success); border: 1px solid var(--success); }}
        .status-warn {{ background: var(--warning-bg); color: var(--warning); border: 1px solid var(--warning); }}
        .status-fail {{ background: var(--error-bg); color: var(--error); border: 1px solid var(--error); }}

        /* Meta bar */
        .meta-bar {{
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 16px 0;
        }}

        .meta-items {{
            display: flex;
            flex-wrap: wrap;
            gap: 32px;
        }}

        .meta-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .meta-label {{
            color: var(--text-muted);
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }}

        .meta-value {{
            color: var(--text-primary);
            font-size: 0.9em;
            font-weight: 500;
        }}

        /* Main content */
        .main {{
            padding: 32px 0 64px;
        }}

        /* Metric cards */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
            margin-bottom: 32px;
        }}

        @media (max-width: 1024px) {{
            .metrics-grid {{ grid-template-columns: repeat(3, 1fr); }}
        }}

        @media (max-width: 640px) {{
            .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}

        .metric-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }}

        .metric-card:hover {{
            background: var(--bg-card-hover);
            border-color: var(--accent-blue);
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
        }}

        .metric-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
            opacity: 0;
            transition: opacity 0.2s ease;
        }}

        .metric-card:hover::before {{
            opacity: 1;
        }}

        .metric-card .value {{
            font-size: 2.2em;
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 4px;
            color: var(--accent-blue);
        }}

        .metric-card .value.success {{ color: var(--success); }}
        .metric-card .value.error {{ color: var(--error); }}
        .metric-card .value.orange {{ color: var(--accent-orange); }}

        .metric-card .label {{
            font-size: 0.8em;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }}

        /* Section */
        .section {{
            margin-bottom: 32px;
        }}

        .section-title {{
            font-size: 1.1em;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 16px;
            padding-left: 12px;
            border-left: 3px solid var(--accent-blue);
        }}

        /* Cards */
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
        }}

        /* Charts grid */
        .charts-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-bottom: 32px;
        }}

        @media (max-width: 900px) {{
            .charts-grid {{ grid-template-columns: 1fr; }}
        }}

        .chart-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
        }}

        .chart-title {{
            font-size: 0.95em;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 16px;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}

        .chart-container {{
            position: relative;
            height: 280px;
        }}

        /* Table */
        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
        }}

        th {{
            color: var(--text-muted);
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }}

        td {{
            padding: 14px 16px;
            border-bottom: 1px solid rgba(42, 53, 72, 0.5);
            font-size: 0.9em;
            font-variant-numeric: tabular-nums;
        }}

        tr:hover td {{
            background: rgba(59, 130, 246, 0.03);
        }}

        .text-success {{ color: var(--success); }}
        .text-error {{ color: var(--error); }}

        .region-badge {{
            background: rgba(59, 130, 246, 0.1);
            color: var(--accent-blue);
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 0.85em;
            font-weight: 600;
            font-family: 'SF Mono', 'Fira Code', monospace;
        }}

        .bar-container {{
            width: 100%;
            height: 6px;
            background: var(--bg-primary);
            border-radius: 3px;
            overflow: hidden;
        }}

        .bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
            border-radius: 3px;
            transition: width 0.6s ease;
        }}

        /* Response time detail grid */
        .rt-grid {{
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 12px;
            margin-top: 16px;
        }}

        @media (max-width: 900px) {{
            .rt-grid {{ grid-template-columns: repeat(4, 1fr); }}
        }}

        .rt-item {{
            text-align: center;
            padding: 16px 8px;
            background: var(--bg-primary);
            border-radius: 8px;
            border: 1px solid var(--border);
        }}

        .rt-item .rt-val {{
            font-size: 1.4em;
            font-weight: 700;
            color: var(--text-primary);
            font-variant-numeric: tabular-nums;
        }}

        .rt-item .rt-label {{
            font-size: 0.7em;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 4px;
            font-weight: 600;
        }}

        /* Footer */
        .footer {{
            text-align: center;
            padding: 32px 0;
            color: var(--text-muted);
            font-size: 0.8em;
            border-top: 1px solid var(--border);
        }}

        .footer a {{
            color: var(--accent-blue);
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container header-content">
            <div class="logo-section">
                <div class="logo-icon">
                    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M24 4C18 4 13 8 12 14C8 14 4 18 4 23C4 28 8 32 13 32H35C40 32 44 28 44 23C44 18.5 41 15 37 14.2C36 8.5 31 4 24 4Z" stroke="url(#cg)" stroke-width="2.5" fill="rgba(59,130,246,0.08)"/>
                        <path d="M16 32L12 44M24 32L24 44M32 32L36 44" stroke="url(#og)" stroke-width="2" stroke-linecap="round"/>
                        <circle cx="12" cy="44" r="2" fill="#f59e0b"/>
                        <circle cx="24" cy="44" r="2" fill="#3b82f6"/>
                        <circle cx="36" cy="44" r="2" fill="#f59e0b"/>
                        <defs>
                            <linearGradient id="cg" x1="4" y1="18" x2="44" y2="18"><stop stop-color="#3b82f6"/><stop offset="1" stop-color="#06b6d4"/></linearGradient>
                            <linearGradient id="og" x1="12" y1="32" x2="36" y2="44"><stop stop-color="#3b82f6"/><stop offset="1" stop-color="#f59e0b"/></linearGradient>
                        </defs>
                    </svg>
                </div>
                <div>
                    <div class="logo-text">cloudstorm</div>
                    <div class="logo-subtitle">Load Test Report</div>
                </div>
            </div>
            <span class="status-badge {status_class}">{status_text}</span>
        </div>
    </div>

    <div class="meta-bar">
        <div class="container meta-items">
            <div class="meta-item">
                <span class="meta-label">Target</span>
                <span class="meta-value">{meta.get('target_url', 'N/A')}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Tool</span>
                <span class="meta-value">{meta.get('tool', 'N/A')}</span>
            </div>
            {'<div class="meta-item"><span class="meta-label">Script</span><span class="meta-value">' + config.get('script', '').split('/')[-1] + '</span></div>' if config.get('script') else '<div class="meta-item"><span class="meta-label">Endpoints</span><span class="meta-value">' + config.get('endpoints', '/') + '</span></div>'}
            <div class="meta-item">
                <span class="meta-label">Users</span>
                <span class="meta-value">{config.get('users', 'N/A')}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Duration</span>
                <span class="meta-value">{config.get('duration', 'N/A')}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Spawn Rate</span>
                <span class="meta-value">{config.get('spawn_rate', 'N/A')}/s</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Regions</span>
                <span class="meta-value">{', '.join(meta.get('regions', []))}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Instances</span>
                <span class="meta-value">{meta.get('total_instances', 'N/A')}</span>
            </div>
        </div>
    </div>

    <div class="container main">
        <!-- Key Metrics -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="value">{total_req:,}</div>
                <div class="label">Total Requests</div>
            </div>
            <div class="metric-card">
                <div class="value success">{success_req:,}</div>
                <div class="label">Successful</div>
            </div>
            <div class="metric-card">
                <div class="value error">{failed_req:,}</div>
                <div class="label">Failed</div>
            </div>
            <div class="metric-card">
                <div class="value orange">{rps:.1f}</div>
                <div class="label">Requests / sec</div>
            </div>
            <div class="metric-card">
                <div class="value {'success' if error_rate == 0 else 'error'}">{error_rate * 100:.2f}%</div>
                <div class="label">Error Rate</div>
            </div>
        </div>

        <!-- Charts -->
        <div class="charts-grid">
            <div class="chart-card">
                <div class="chart-title">Response Time Percentiles</div>
                <div class="chart-container">
                    <canvas id="percentilesChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-title">{'Requests by Region' if len(by_region) > 1 else 'Request Distribution'}</div>
                <div class="chart-container">
                    <canvas id="regionChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Response Times Detail -->
        <div class="section">
            <div class="section-title">Response Times (ms)</div>
            <div class="card">
                <div class="rt-grid">
                    <div class="rt-item">
                        <div class="rt-val">{min_ms:.2f}</div>
                        <div class="rt-label">Min</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{avg_ms:.2f}</div>
                        <div class="rt-label">Avg</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{median_ms:.2f}</div>
                        <div class="rt-label">Median</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{p90_ms:.2f}</div>
                        <div class="rt-label">p90</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{p95_ms:.2f}</div>
                        <div class="rt-label">p95</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{p99_ms:.2f}</div>
                        <div class="rt-label">p99</div>
                    </div>
                    <div class="rt-item">
                        <div class="rt-val">{max_ms:.2f}</div>
                        <div class="rt-label">Max</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Endpoints Breakdown -->
        {'<div class="section"><div class="section-title">Endpoints</div><div class="card" style="overflow-x: auto;"><table><thead><tr><th>Endpoint</th><th>Requests</th><th>Successful</th><th>Failed</th><th>Avg (ms)</th><th>Min (ms)</th><th>Max (ms)</th><th>Error Rate</th></tr></thead><tbody>' + endpoint_rows + '</tbody></table></div></div>' if endpoint_rows else ''}

        <!-- Region Breakdown -->
        <div class="section">
            <div class="section-title">Results by Region</div>
            <div class="card" style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Region</th>
                            <th>Requests</th>
                            <th>Successful</th>
                            <th>Failed</th>
                            <th>Avg (ms)</th>
                            <th>p95 (ms)</th>
                            <th>Req/s</th>
                            <th>Error Rate</th>
                            <th style="width: 120px">Load Share</th>
                        </tr>
                    </thead>
                    <tbody>{region_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="footer">
        <div class="container">
            Generated by <strong>cloudstorm</strong> on {timestamp}
        </div>
    </div>

    <script>
        Chart.defaults.color = '#9ca3af';
        Chart.defaults.borderColor = 'rgba(42, 53, 72, 0.5)';
        Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";

        // Percentiles chart
        new Chart(document.getElementById('percentilesChart'), {{
            type: 'bar',
            data: {{
                labels: ['Min', 'Avg', 'Median', 'p90', 'p95', 'p99', 'Max'],
                datasets: [{{
                    label: 'Response Time (ms)',
                    data: [{min_ms:.2f}, {avg_ms:.2f}, {median_ms:.2f}, {p90_ms:.2f}, {p95_ms:.2f}, {p99_ms:.2f}, {max_ms:.2f}],
                    backgroundColor: [
                        'rgba(16, 185, 129, 0.8)',
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(6, 182, 212, 0.8)',
                        'rgba(245, 158, 11, 0.6)',
                        'rgba(245, 158, 11, 0.8)',
                        'rgba(239, 68, 68, 0.6)',
                        'rgba(239, 68, 68, 0.8)'
                    ],
                    borderColor: [
                        '#10b981', '#3b82f6', '#06b6d4',
                        '#f59e0b', '#f59e0b', '#ef4444', '#ef4444'
                    ],
                    borderWidth: 1,
                    borderRadius: 6,
                    borderSkipped: false,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        backgroundColor: '#1a2234',
                        titleColor: '#e5e7eb',
                        bodyColor: '#e5e7eb',
                        borderColor: '#2a3548',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {{
                            label: function(ctx) {{ return ctx.parsed.y.toFixed(2) + ' ms'; }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        grid: {{ color: 'rgba(42, 53, 72, 0.3)' }},
                        ticks: {{ callback: function(v) {{ return v + ' ms'; }} }}
                    }},
                    x: {{
                        grid: {{ display: false }}
                    }}
                }}
            }}
        }});

        // Region chart
        const regionLabels = [{', '.join(region_labels_js)}];
        const regionData = [{', '.join(region_reqs_js)}];
        const regionAvg = [{', '.join(region_avg_js)}];
        const regionP95 = [{', '.join(region_p95_js)}];

        if (regionLabels.length > 1) {{
            new Chart(document.getElementById('regionChart'), {{
                type: 'bar',
                data: {{
                    labels: regionLabels,
                    datasets: [
                        {{
                            label: 'Requests',
                            data: regionData,
                            backgroundColor: 'rgba(59, 130, 246, 0.7)',
                            borderColor: '#3b82f6',
                            borderWidth: 1,
                            borderRadius: 6,
                            yAxisID: 'y',
                        }},
                        {{
                            label: 'Avg (ms)',
                            data: regionAvg,
                            backgroundColor: 'rgba(245, 158, 11, 0.7)',
                            borderColor: '#f59e0b',
                            borderWidth: 1,
                            borderRadius: 6,
                            yAxisID: 'y1',
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ position: 'top' }},
                        tooltip: {{
                            backgroundColor: '#1a2234',
                            borderColor: '#2a3548',
                            borderWidth: 1,
                            cornerRadius: 8,
                            padding: 12,
                        }}
                    }},
                    scales: {{
                        y: {{
                            beginAtZero: true,
                            position: 'left',
                            grid: {{ color: 'rgba(42, 53, 72, 0.3)' }},
                            title: {{ display: true, text: 'Requests' }}
                        }},
                        y1: {{
                            beginAtZero: true,
                            position: 'right',
                            grid: {{ drawOnChartArea: false }},
                            title: {{ display: true, text: 'Avg (ms)' }}
                        }},
                        x: {{ grid: {{ display: false }} }}
                    }}
                }}
            }});
        }} else {{
            // Single region - show success vs failed donut
            new Chart(document.getElementById('regionChart'), {{
                type: 'doughnut',
                data: {{
                    labels: ['Successful', 'Failed'],
                    datasets: [{{
                        data: [{success_req}, {failed_req}],
                        backgroundColor: ['rgba(16, 185, 129, 0.8)', 'rgba(239, 68, 68, 0.8)'],
                        borderColor: ['#10b981', '#ef4444'],
                        borderWidth: 2,
                        hoverOffset: 8,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '65%',
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ padding: 20 }} }},
                        tooltip: {{
                            backgroundColor: '#1a2234',
                            borderColor: '#2a3548',
                            borderWidth: 1,
                            cornerRadius: 8,
                            padding: 12,
                        }}
                    }}
                }}
            }});
        }}
    </script>
</body>
</html>"""

    return html

