You have live data and a React component template. Generate an interactive operations dashboard as a **React artifact** right now.

**How to build it:**
1. Use the `template` field as your starting point — it's a complete React component
2. Replace all hardcoded sample data with the live data from `dashboard` and `incidents`
3. The "Duty Officer" stat card should show the Chief Officer section crew member
4. Do NOT include Administration or Time Off sections in the crew list
5. Each dispatch call should show its report status from the `report` field
6. On the Overview tab, show a green checkmark for calls with reports, amber "No report" for calls missing one
7. The Reporting tab should have "Start Report" buttons for calls without reports — when clicked, show hint: "Ask Claude: Start a report for {dispatch_id}"
8. Display the SJI Fire logo in the upper-left header corner with an onError fallback
9. Logo URL: https://res.cloudinary.com/san-juan-fire-district-3/image/fetch/f_auto/https://www.sjifire.org/assets/sjifire-logo-clear.png
10. Show "Last Updated: {time}" in the header using the dashboard timestamp, with a subtle "ask Claude to refresh" hint

**Refreshing:**
When the user asks to refresh (e.g., "refresh", "update", "get latest"), call `start_session` again and regenerate the artifact with fresh data.

**Starting a report:**
When the user clicks "Start Report" or mentions a dispatch ID, switch to the incident reporting workflow — call `get_dispatch_call` for that ID and begin creating an incident report.
