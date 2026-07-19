from django import forms
from .models import Comment, Location

class CommentForm(forms.ModelForm):
    """Form for adding comments to persons"""
    class Meta:
        model = Comment
        fields = ['message']
        widgets = {
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Enter your comment here...'
            })
        }

class LocationUpdateForm(forms.ModelForm):
    """Form for updating location information"""
    class Meta:
        model = Location
        fields = ['description', 'address', 'location']
        widgets = {
            'description': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Location description'
            }),
            'address': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Full address'
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'latitude,longitude (e.g., 40.7128,-74.0060)'
            })
        }

from django.contrib.auth.models import User
from .models import UserProfile

class UserProfileUpdateForm(forms.ModelForm):
    """Form for updating user's own profile info"""
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email Address'}),
        }

class UserLocationAssignmentForm(forms.ModelForm):
    """Form for admins to assign locations to a user profile"""
    class Meta:
        model = UserProfile
        fields = ['locations']
        widgets = {
            'locations': forms.CheckboxSelectMultiple(attrs={
                'class': 'form-check-input'
            })
        }
 