import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('nilm', '0008_alter_device_location'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EventLabel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('appliance_type', models.CharField(
                    choices=[
                        ('FRIDGE', 'Fridge'),
                        ('OVEN', 'Oven'),
                        ('KETTLE', 'Kettle'),
                        ('MICROWAVE', 'Microwave'),
                        ('WASHING MACHINE', 'Washing Machine'),
                        ('IRON', 'Iron'),
                        ('LIGHTS', 'Lights'),
                        ('TV', 'TV'),
                        ('OTHER', 'Otro / sin identificar'),
                    ],
                    max_length=20,
                )),
                ('notes', models.CharField(blank=True, default='', max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('labeled_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ('measurement', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='label',
                    to='nilm.measurement',
                )),
            ],
            options={
                'verbose_name': 'Etiqueta de evento',
                'verbose_name_plural': 'Etiquetas de eventos',
            },
        ),
    ]
