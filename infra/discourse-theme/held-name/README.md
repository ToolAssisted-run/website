# Held name notice (Discourse theme component)

Signup refuses a held name the way it refuses a taken one: "Not available.
Try Nymx1?". The one person that name belongs to is exactly the person that
message misleads. This component asks the archivist
(`/archivist/api/name/status`) what stands in the way and, when the name is
held, says so and points at the claim.

Install (admin, once):

1. `cd infra/discourse-theme && python3 -c "import shutil; shutil.make_archive('held-name', 'zip', '.', 'held-name')"`
2. Forum admin -> Customize -> Themes -> Install -> From your device,
   pick `held-name.zip`.
3. Add the component to the active theme (Themes -> the site theme ->
   Components -> Add).

Nothing here holds the reserved list: the archivist reads it from the forum's
own `reserved_usernames` setting, so a claim that frees a name frees the
notice with it, within ten minutes.
