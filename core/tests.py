from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .admin_account import admin_account_exists


class AdminAccountSetupTests(TestCase):
    def test_admin_login_shows_setup_link_when_no_admin_exists(self):
        response = self.client.get(reverse('admin_login'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(admin_account_exists())
        self.assertContains(response, 'Create the first admin account')

    def test_admin_account_setup_creates_superuser(self):
        response = self.client.post(reverse('admin_account_setup'), {
            'username': 'admin1',
            'email': 'admin1@example.com',
            'first_name': 'Admin',
            'last_name': 'One',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
        })

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='admin1')
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(User.objects.filter(is_superuser=True).exists())