#!/usr/bin/env python3
"""Compare Aladtec member fields with Entra ID user fields."""

import asyncio
import logging

from sjifire.aladtec.scraper import AladtecScraper
from sjifire.core.config import load_entra_sync_config
from sjifire.entra.users import EntraUserManager

# Suppress noisy HTTP logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("msal").setLevel(logging.WARNING)


async def compare_fields() -> None:
    """Compare fields between Aladtec and Entra ID."""
    # Load config
    config = load_entra_sync_config()

    # Fetch Aladtec members
    print("Fetching Aladtec members...")
    with AladtecScraper() as scraper:
        scraper.login()
        members = scraper.get_members()

    # Fetch Entra users
    print("Fetching Entra ID users...")
    user_manager = EntraUserManager()
    entra_users = await user_manager.get_users(include_disabled=True)

    # Build lookup by email
    entra_by_email = {u.email.lower(): u for u in entra_users if u.email}
    entra_by_upn = {u.upn.lower(): u for u in entra_users if u.upn}

    print("\n" + "=" * 80)
    print("FIELD COMPARISON: Aladtec vs Entra ID")
    print("=" * 80)

    updates_needed = []

    # Build skip list from config
    skip_emails = {e.lower() for e in config.skip_emails}

    for member in members:
        if not member.email or not member.email.endswith("@sjifire.org"):
            continue

        email_lower = member.email.lower()

        # Skip emails in skip list
        if email_lower in skip_emails:
            continue

        entra_user = entra_by_email.get(email_lower) or entra_by_upn.get(email_lower)

        if not entra_user:
            continue

        # Compare fields
        diffs = []

        # Build expected display name with display_rank prefix
        if member.display_rank:
            expected_display = f"{member.display_rank} {member.first_name} {member.last_name}"
        else:
            expected_display = member.display_name

        # Basic fields
        if entra_user.first_name != member.first_name:
            diffs.append(f"  firstName: '{entra_user.first_name}' → '{member.first_name}'")
        if entra_user.last_name != member.last_name:
            diffs.append(f"  lastName: '{entra_user.last_name}' → '{member.last_name}'")
        if entra_user.display_name != expected_display:
            diffs.append(f"  displayName: '{entra_user.display_name}' → '{expected_display}'")

        # Employee ID
        if member.employee_id and entra_user.employee_id != member.employee_id:
            diffs.append(f"  employeeId: '{entra_user.employee_id}' → '{member.employee_id}'")

        # Job title
        if member.job_title and entra_user.job_title != member.job_title:
            diffs.append(f"  jobTitle: '{entra_user.job_title}' → '{member.job_title}'")

        # Work group (employee type)
        if member.work_group and entra_user.employee_type != member.work_group:
            diffs.append(f"  employeeType: '{entra_user.employee_type}' → '{member.work_group}'")

        # Mobile phone
        if member.phone and entra_user.mobile_phone != member.phone:
            diffs.append(f"  mobilePhone: '{entra_user.mobile_phone}' → '{member.phone}'")

        # Home phone (business phones)
        if member.home_phone:
            existing_phones = entra_user.business_phones or []
            if member.home_phone not in existing_phones:
                diffs.append(f"  businessPhones: {existing_phones} → ['{member.home_phone}']")

        # Office location (station)
        if member.office_location and entra_user.office_location != member.office_location:
            diffs.append(
                f"  officeLocation: '{entra_user.office_location}' → '{member.office_location}'"
            )

        # Hire date
        if member.date_hired:
            member_date = member.date_hired.replace("/", "-")
            if entra_user.employee_hire_date:
                entra_date = entra_user.employee_hire_date[:10]
                if not entra_user.employee_hire_date.startswith(member_date):
                    if member_date > entra_date:
                        # Aladtec is newer - flag as conflict
                        diffs.append(
                            f"  hireDate: ⚠️ CONFLICT - Entra={entra_date} "
                            f"is OLDER than Aladtec={member_date}"
                        )
                    else:
                        diffs.append(
                            f"  hireDate: '{entra_user.employee_hire_date}' → '{member.date_hired}'"
                        )
            else:
                diffs.append(f"  hireDate: None → '{member.date_hired}'")

        # Personal email
        if member.personal_email and entra_user.personal_email != member.personal_email:
            diffs.append(
                f"  personalEmail: '{entra_user.personal_email}' → '{member.personal_email}'"
            )

        # Company name
        if entra_user.company_name != config.company_name:
            diffs.append(f"  companyName: '{entra_user.company_name}' → '{config.company_name}'")

        # Extension attributes
        # extensionAttribute1 = rank
        if entra_user.extension_attribute1 != member.rank:
            diffs.append(
                f"  extAttr1 (rank): '{entra_user.extension_attribute1}' → '{member.rank}'"
            )
        # extensionAttribute2 = EVIP
        if entra_user.extension_attribute2 != member.evip:
            diffs.append(
                f"  extAttr2 (evip): '{entra_user.extension_attribute2}' → '{member.evip}'"
            )
        # extensionAttribute3 = positions (comma-delimited)
        positions_str = ",".join(member.positions) if member.positions else None
        if entra_user.extension_attribute3 != positions_str:
            diffs.append(
                f"  extAttr3 (positions): '{entra_user.extension_attribute3}' → '{positions_str}'"
            )

        if diffs:
            updates_needed.append((member.display_name, diffs))

    # Print results
    print(f"\nUsers with field differences: {len(updates_needed)}\n")

    for name, diffs in updates_needed:
        print(f"┌─ {name}")
        print("│")
        print("│  {:25} {:30} {:30}".format("Field", "Current (Entra)", "New (Aladtec)"))
        print("│  " + "-" * 85)
        for diff in diffs:
            # Parse the diff string to extract field, old, new values
            if "→" in diff:
                parts = diff.split(":", 1)
                field = parts[0].strip()
                values = parts[1].strip() if len(parts) > 1 else ""
                if "→" in values:
                    old_new = values.split("→")
                    old_val = old_new[0].strip().strip("'")
                    new_val = old_new[1].strip().strip("'") if len(old_new) > 1 else ""
                    # Truncate long values
                    if len(old_val) > 28:
                        old_val = old_val[:25] + "..."
                    if len(new_val) > 28:
                        new_val = new_val[:25] + "..."
                    print(f"│  {field:25} {old_val:30} {new_val:30}")
                else:
                    print(f"│  {diff}")
            else:
                print(f"│  {diff}")
        print("└" + "─" * 90)
        print()

    # Summary by field
    print("=" * 80)
    print("SUMMARY BY FIELD")
    print("=" * 80)
    field_counts = {}
    for _, diffs in updates_needed:
        for diff in diffs:
            field = diff.split(":")[0].strip()
            field_counts[field] = field_counts.get(field, 0) + 1

    for field, count in sorted(field_counts.items(), key=lambda x: -x[1]):
        print(f"  {field}: {count} users")


if __name__ == "__main__":
    asyncio.run(compare_fields())
