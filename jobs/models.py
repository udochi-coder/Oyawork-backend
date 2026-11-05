from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=40, blank=True, help_text="Icon name used by the frontend")

    class Meta:
        db_table = "category"
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Skills(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="skills")

    class Meta:
        db_table = "jobs_skills"
        ordering = ["name"]
        verbose_name_plural = "skills"

    def __str__(self):
        return self.name


class Jobs(models.Model):
    JOB_STATUS_CHOICES = [
        ("open", "Open"),
        ("assigned", "Assigned"),
        ("completed", "Completed"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]

    employer = models.ForeignKey("accounts.Employer", on_delete=models.CASCADE, related_name="jobs")
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="jobs")
    required_skills = models.ManyToManyField(Skills, related_name="jobs", blank=True)
    offered_price = models.DecimalField(max_digits=10, decimal_places=2)
    location = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    preferred_radius = models.PositiveIntegerField(default=10, help_text="Maximum distance in kilometers")
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    estimated_duration = models.PositiveIntegerField(help_text="Estimated duration in hours", default=10)
    status = models.CharField(choices=JOB_STATUS_CHOICES, max_length=20, default="open")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_posted = models.BooleanField(default=False)

    class Meta:
        db_table = "jobs"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
