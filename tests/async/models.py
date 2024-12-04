from django.db import models
from django.utils import timezone


class RelatedModel(models.Model):
    simple = models.ForeignKey("SimpleModel", models.CASCADE, null=True)


class SimpleModel(models.Model):
    field = models.IntegerField()
    created = models.DateTimeField(default=timezone.now)


class ManyToManyModel(models.Model):
    simples = models.ManyToManyField("SimpleModel")


class ModelWithSyncOverride(models.Model):
    field = models.IntegerField()

    def save(self, *args, **kwargs):
        # we increment our field right before saving
        self.field += 1
        super().save(*args, **kwargs)
