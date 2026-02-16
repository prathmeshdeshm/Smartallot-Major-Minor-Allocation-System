from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from allotment.models import Student


class StudentRegistrationForm(UserCreationForm):
    roll_no = forms.CharField(
        max_length=20,
        required=True,
        help_text='Enter your unique roll number.'
    )
    department = forms.ChoiceField(
        choices=Student.DEPARTMENTS,
        required=True
    )
    percentage = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=True,
        help_text='Enter your academic percentage (e.g., 85.50).'
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('first_name', 'last_name', 'email')

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            full_name = f"{self.cleaned_data.get('first_name')} {self.cleaned_data.get('last_name')}".strip()
            Student.objects.create(
                user=user,
                name=full_name or user.username,
                roll_no=self.cleaned_data.get('roll_no'),
                department=self.cleaned_data.get('department'),
                percentage=self.cleaned_data.get('percentage'),
                email=self.cleaned_data.get('email'),
            )
        return user


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(help_text="Enter the email you registered with.")


class OTPPasswordResetForm(forms.Form):
    otp = forms.CharField(
        max_length=6,
        label="OTP",
        help_text="Enter the 6-digit code sent to your email."
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput,
        label="New password"
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput,
        label="Confirm new password"
    )

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("new_password1")
        p2 = cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data
