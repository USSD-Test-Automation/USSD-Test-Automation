# forms.py
from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                   BooleanField, TextAreaField, SelectMultipleField) # Added SelectMultipleField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError, Optional, Regexp
from wtforms.widgets import ListWidget, CheckboxInput # For SelectMultipleField with checkboxes

from models import User # Assuming models.py is in the same directory or accessible

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=50)])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Login')

class CreateUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    # password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])

    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message="Password must be at least 8 characters long."),
        Regexp(r'.*[0-9].*', message="Password must contain at least one number."),
        Regexp(r'.*[\W_].*', message="Password must contain at least one special character.")
    ])

    confirm_password = PasswordField('Confirm Password',
                                     validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    role = SelectField('Role', choices=[('admin', 'Admin'), ('manager', 'Manager'), ('tester', 'Tester')],
                       validators=[DataRequired()])
    submit = SubmitField('Create User')

    def validate_username(self, username):
        if User.find_by_username(username.data): # Ensure User.find_by_username is efficient
            raise ValidationError('That username is already taken. Please choose a different one.')

class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=50)])
    role = SelectField('Role', choices=[('admin', 'Admin'), ('manager', 'Manager'), ('tester', 'Tester')],
                       validators=[DataRequired()])

    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message="Password must be at least 8 characters long."),
        Regexp(r'.*[0-9].*', message="Password must contain at least one number."),
        Regexp(r'.*[\W_].*', message="Password must contain at least one special character.")
    ])
    
    confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('password', message='Passwords must match if new password is set.')])
    submit = SubmitField('Update User')

    original_username = None

    def __init__(self, original_username=None, *args, **kwargs): # Made original_username optional for general use
        super(EditUserForm, self).__init__(*args, **kwargs)
        self.original_username = original_username

    def validate_username(self, username_field):
        if self.original_username and username_field.data != self.original_username:
            user = User.find_by_username(username_field.data)
            if user:
                raise ValidationError('That username is already taken by another user. Please choose a different one.')

# --- Base Form for Batch Assignments ---
class BaseAssignmentForm(FlaskForm):
    tester_id = SelectField('Assign to Tester', coerce=int, validators=[DataRequired("Please select a tester.")])
    priority = SelectField(
        'Priority',
        choices=[('HIGH', 'High'), ('MEDIUM', 'Medium'), ('LOW', 'Low')],
        default='MEDIUM',
        validators=[DataRequired("Please select a priority level.")]
    )
    notes = TextAreaField(
        'Notes',
        validators=[Optional(), Length(max=500)],
        render_kw={"rows": 3, "placeholder": "Optional: Add any specific instructions or notes here."}
    )
    # Submit button will be added in inheriting classes or templates

# --- Forms for Assigning Batches ---
class AssignTestCaseForm(BaseAssignmentForm): # Modified to inherit for consistency
    # This form is specific to a single test case.
    # It's largely the same as BaseAssignmentForm for fields, but conceptually different.
    submit = SubmitField('Assign Test Case')

class AssignSuiteForm(BaseAssignmentForm):
    submit = SubmitField('Assign Entire Suite')

class AssignApplicationForm(BaseAssignmentForm):
    submit = SubmitField('Assign All Tests for Application')

class AssignCustomGroupForm(BaseAssignmentForm):
    submit = SubmitField('Assign Custom Group')

# --- Form for Creating/Editing Custom Test Groups ---
class CreateEditCustomGroupForm(FlaskForm):
    name = StringField('Group Name', validators=[DataRequired(), Length(min=3, max=100)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=500)],
                                render_kw={"rows": 3})
    test_cases = SelectMultipleField(
        'Select Test Cases',
        coerce=int,
        validators=[DataRequired("Please select at least one test case.")],
        widget=ListWidget(prefix_label=False), # Use ListWidget for better layout control
        option_widget=CheckboxInput()          # Render options as checkboxes
        # choices will be populated dynamically in the route
    )
    submit = SubmitField('Save Custom Group')