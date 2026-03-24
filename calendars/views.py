from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import CalendarForm, ShareCreateForm, ShareUpdateForm
from .models import Calendar, CalendarShare
from .permissions import can_manage_calendar


def _get_calendar_for_manage(user, calendar_id):
    calendar = get_object_or_404(Calendar, id=calendar_id)
    if not can_manage_calendar(calendar, user):
        raise PermissionDenied
    return calendar


@login_required
def calendar_list(request):
    owned_calendars = Calendar.objects.filter(owner=request.user)
    shared_calendars = (
        Calendar.objects.filter(
            shares__user=request.user,
            shares__accepted_at__isnull=False,
        )
        .exclude(owner=request.user)
        .select_related("owner")
        .distinct()
    )
    pending_invites = (
        CalendarShare.objects.filter(user=request.user, accepted_at__isnull=True)
        .select_related("calendar", "calendar__owner")
        .order_by("calendar__owner__username", "calendar__name")
    )
    return render(
        request,
        "calendars/list.html",
        {
            "owned_calendars": owned_calendars,
            "shared_calendars": shared_calendars,
            "pending_invites": pending_invites,
        },
    )


@login_required
def calendar_create(request):
    if request.method == "POST":
        form = CalendarForm(request.POST)
        if form.is_valid():
            calendar = form.save(commit=False)
            calendar.owner = request.user
            calendar.save()
            messages.success(request, "Calendar created.")
            return redirect("calendars:edit", calendar_id=calendar.id)
    else:
        form = CalendarForm()

    return render(request, "calendars/form.html", {"form": form, "is_create": True})


@login_required
def calendar_edit(request, calendar_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)

    if request.method == "POST":
        form = CalendarForm(request.POST, instance=calendar)
        if form.is_valid():
            form.save()
            messages.success(request, "Calendar updated.")
            return redirect("calendars:edit", calendar_id=calendar.id)
    else:
        form = CalendarForm(instance=calendar)

    return render(
        request,
        "calendars/form.html",
        {"form": form, "calendar": calendar, "is_create": False},
    )


@login_required
def calendar_delete(request, calendar_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)

    if request.method == "POST":
        calendar.delete()
        messages.success(request, "Calendar deleted.")
        return redirect("calendars:list")

    return render(request, "calendars/delete_confirm.html", {"calendar": calendar})


@login_required
def calendar_sharing(request, calendar_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)
    share_form = ShareCreateForm(calendar=calendar)
    shares = calendar.shares.select_related("user").order_by("user__username")

    return render(
        request,
        "calendars/sharing.html",
        {
            "calendar": calendar,
            "shares": shares,
            "share_form": share_form,
        },
    )


@login_required
def calendar_share_add(request, calendar_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)
    if request.method != "POST":
        return redirect("calendars:sharing", calendar_id=calendar.id)

    form = ShareCreateForm(request.POST, calendar=calendar)
    if form.is_valid():
        CalendarShare.objects.create(
            calendar=calendar,
            user=form.target_user,
            role=form.cleaned_data["role"],
        )
        messages.success(request, "Invitation sent.")
        return redirect("calendars:sharing", calendar_id=calendar.id)

    shares = calendar.shares.select_related("user").order_by("user__username")
    return render(
        request,
        "calendars/sharing.html",
        {
            "calendar": calendar,
            "shares": shares,
            "share_form": form,
        },
    )


@login_required
def calendar_share_update(request, calendar_id, share_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)
    share = get_object_or_404(CalendarShare, id=share_id, calendar=calendar)

    if request.method == "POST":
        form = ShareUpdateForm(request.POST, instance=share)
        if form.is_valid():
            form.save()
            messages.success(request, "Share updated.")

    return redirect("calendars:sharing", calendar_id=calendar.id)


@login_required
def calendar_share_delete(request, calendar_id, share_id):
    calendar = _get_calendar_for_manage(request.user, calendar_id)
    share = get_object_or_404(CalendarShare, id=share_id, calendar=calendar)

    if request.method == "POST":
        share.delete()
        messages.success(request, "Share removed.")

    return redirect("calendars:sharing", calendar_id=calendar.id)


@login_required
def share_invite_accept(request, share_id):
    share = get_object_or_404(CalendarShare, id=share_id, user=request.user)
    if request.method == "POST":
        share.accepted_at = timezone.now()
        share.save(update_fields=["accepted_at", "updated_at"])
        messages.success(request, f"Accepted invite to {share.calendar.name}.")
    return redirect("calendars:list")


@login_required
def share_invite_decline(request, share_id):
    share = get_object_or_404(CalendarShare, id=share_id, user=request.user)
    if request.method == "POST":
        calendar_name = share.calendar.name
        share.delete()
        messages.success(request, f"Declined invite to {calendar_name}.")
    return redirect("calendars:list")
