from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse


class AccountFlowTests(TestCase):
    def test_register_page_loads(self):
        response = self.client.get(reverse("register"))
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertContains(response, "Create account")

    def test_register_creates_user(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "alice",
                "email": "alice@example.com",
                "password1": "safe-password-12345",
                "password2": "safe-password-12345",
            },
        )
        self.assertRedirects(response, reverse("login"))
        self.assertTrue(User.objects.filter(username="alice").exists())

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('home')}")

    def test_login_redirects_to_dashboard(self):
        user = User.objects.create_user(
            username="bob", password="strong-password-12345"
        )
        response = self.client.post(
            reverse("login"),
            {"username": user.username, "password": "strong-password-12345"},
        )
        self.assertRedirects(response, reverse("home"))

    def test_logout_clears_session(self):
        user = User.objects.create_user(
            username="carol", password="strong-password-12345"
        )
        self.client.login(username=user.username, password="strong-password-12345")

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        home_response = self.client.get(reverse("home"))
        self.assertRedirects(
            home_response,
            f"{reverse('login')}?next={reverse('home')}",
        )
