from django import forms
from django.contrib.auth.models import User

from .models import Calendar, CalendarShare


class CalendarForm(forms.ModelForm):
    class Meta:
        model = Calendar
        fields = ["slug", "name", "description", "color", "timezone"]


class ShareCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    role = forms.ChoiceField(choices=CalendarShare.ROLE_CHOICES)

    def __init__(self, *args, calendar=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.calendar = calendar
        self.target_user = None

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise forms.ValidationError("No user found with that username.") from exc

        if self.calendar and user == self.calendar.owner:
            raise forms.ValidationError("Calendar owner already has full access.")

        if self.calendar and self.calendar.shares.filter(user=user).exists():
            raise forms.ValidationError("That user already has access.")

        self.target_user = user
        return username


class ShareUpdateForm(forms.ModelForm):
    class Meta:
        model = CalendarShare
        fields = ["role"]
