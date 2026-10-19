"""
Locust custom script example for cloudstorm.

This script simulates an authenticated user flow:
1. Login to get a JWT token
2. Browse products (weighted 3x)
3. Add item to cart (weighted 1x)

Usage:
    cloudstorm run https://api.example.com \
        --script custom_scripts_examples/locust_login_flow.py \
        --tool locust -u 1000 -d 60s

Environment variables available from cloudstorm:
    TARGET_URL, USERS, SPAWN_RATE, DURATION, ENDPOINTS
"""
import os

from locust import HttpUser, between, task


class AuthenticatedUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Login and store authentication token."""
        response = self.client.post("/api/auth/login", json={
            "username": os.environ.get("TEST_USERNAME", "testuser"),
            "password": os.environ.get("TEST_PASSWORD", "testpass123"),
        })
        self.token = ""
        self.headers = {}
        if response.status_code == 200:
            try:
                self.token = response.json().get("token", "")
            except ValueError:
                pass  # Non-JSON response: continue unauthenticated
            if self.token:
                self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(3)
    def browse_products(self):
        """Browse product listing -- most common action."""
        self.client.get("/api/products", headers=self.headers, name="/api/products")

    @task(1)
    def view_product_detail(self):
        """View a specific product."""
        self.client.get("/api/products/1", headers=self.headers, name="/api/products/:id")

    @task(1)
    def add_to_cart(self):
        """Add an item to the cart."""
        self.client.post("/api/cart", json={"product_id": 1, "quantity": 1},
                        headers=self.headers, name="/api/cart")
