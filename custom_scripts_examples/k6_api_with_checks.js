/**
 * k6 custom script example for cloudstorm.
 *
 * This script tests an API with:
 * - Custom headers (API key authentication)
 * - Response validation (status codes, response time, body content)
 * - Multiple endpoints with different weights
 *
 * Usage:
 *     cloudstorm run https://api.example.com \
 *         --script custom_scripts_examples/k6_api_with_checks.js \
 *         --tool k6 -u 5000 -d 60s
 *
 * Environment variables available from cloudstorm:
 *     __ENV.TARGET_URL, __ENV.USERS, __ENV.DURATION, __ENV.ENDPOINTS
 *
 * You can pass extra env vars via the shell:
 *     export API_KEY=your-key && cloudstorm run ...
 */
import http from 'k6/http';
import { check, sleep, group } from 'k6';

export const options = {
    vus: parseInt(__ENV.USERS || '100'),
    duration: __ENV.DURATION || '60s',
    thresholds: {
        http_req_duration: ['p(95)<500'],
        http_req_failed: ['rate<0.01'],
    },
};

const BASE_URL = __ENV.TARGET_URL;
const API_KEY = __ENV.API_KEY || 'test-api-key';

const headers = {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
};

export default function () {
    // Browse products (60% of traffic)
    group('Browse Products', function () {
        const res = http.get(`${BASE_URL}/api/products`, { headers });
        check(res, {
            'products: status 200': (r) => r.status === 200,
            'products: response time < 500ms': (r) => r.timings.duration < 500,
            'products: has body': (r) => r.body.length > 0,
        });
    });

    sleep(0.5);

    // Search (20% of traffic)
    if (Math.random() < 0.33) {
        group('Search', function () {
            const res = http.get(`${BASE_URL}/api/search?q=test`, { headers });
            check(res, {
                'search: status 200': (r) => r.status === 200,
            });
        });
    }

    // Create order (10% of traffic)
    if (Math.random() < 0.1) {
        group('Create Order', function () {
            const payload = JSON.stringify({
                product_id: Math.floor(Math.random() * 100) + 1,
                quantity: 1,
            });
            const res = http.post(`${BASE_URL}/api/orders`, payload, { headers });
            check(res, {
                'order: status 201 or 200': (r) => r.status === 201 || r.status === 200,
            });
        });
    }

    sleep(Math.random() * 1.5 + 0.5);
}

// handleSummary is auto-appended by cloudstorm if missing,
// but you can define your own for custom output:
export function handleSummary(data) {
    return {
        'summary.json': JSON.stringify(data),
        'stdout': '',
    };
}
