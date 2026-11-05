from django.db import migrations

# Categories and skills from the OyaWork "What jobs can you do?" screen.
# Each category gets a skill with the same name so jobs and workers line up.
SEED = {
    "Cleaning": ("sparkles", ["Cleaning", "Professional Cleaning", "Post-Event Clean"]),
    "Laundry": ("shirt", ["Laundry", "Deep Home Laundry", "Ironing"]),
    "Plumbing": ("wrench", ["Plumbing"]),
    "Painting": ("paint-roller", ["Painting"]),
    "Gardening": ("leaf", ["Gardening"]),
    "Driving": ("car", ["Driving"]),
    "Cooking": ("chef-hat", ["Cooking"]),
    "Babysitting": ("baby", ["Babysitting"]),
    "Elderly Care": ("heart-handshake", ["Elderly Care", "Elderly Care Support"]),
    "Electrical": ("zap", ["Electrician", "Generator Repair"]),
    "Carpentry": ("hammer", ["Carpentry"]),
    "Moving/Loading": ("package", ["Moving/Loading"]),
    "Hairdressing": ("scissors", ["Hairdressing"]),
    "Barbing": ("scissors", ["Barbing"]),
    "General Handyman": ("tool", ["General Handyman"]),
    "Errand Services": ("shopping-bag", ["Errand Services"]),
}


def seed(apps, schema_editor):
    Category = apps.get_model("jobs", "Category")
    Skills = apps.get_model("jobs", "Skills")
    for name, (icon, skills) in SEED.items():
        category, _ = Category.objects.get_or_create(name=name, defaults={"icon": icon})
        if not category.icon:
            category.icon = icon
            category.save(update_fields=["icon"])
        for skill in skills:
            Skills.objects.get_or_create(name=skill, defaults={"category": category})


class Migration(migrations.Migration):
    dependencies = [("jobs", "0003_alter_category_options_alter_skills_options_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
