use std::collections::HashMap;

#[derive(Debug, Default, Clone)]
pub struct FormErrors {
    pub fields: HashMap<String, Vec<String>>,
    pub general: Vec<String>,
}

impl FormErrors {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_field_error(&mut self, field: impl Into<String>, error: impl Into<String>) {
        let field = field.into();
        let error = error.into();
        self.fields.entry(field).or_default().push(error);
    }

    pub fn add_general_error(&mut self, error: impl Into<String>) {
        self.general.push(error.into());
    }

    pub fn has_errors(&self) -> bool {
        !self.fields.is_empty() || !self.general.is_empty()
    }

    pub fn get_field_errors(&self, field: &str) -> Option<&Vec<String>> {
        self.fields.get(field)
    }
}

pub fn validate_required(value: &str, field_name: &str, errors: &mut FormErrors) {
    if value.trim().is_empty() {
        errors.add_field_error(field_name, format!("{} is required", field_name));
    }
}

pub fn validate_min_length(value: &str, field_name: &str, min: usize, errors: &mut FormErrors) {
    if value.len() < min {
        errors.add_field_error(
            field_name,
            format!("{} must be at least {} characters", field_name, min),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_required_rejects_empty_values() {
        let mut errors = FormErrors::new();

        validate_required("   ", "username", &mut errors);

        assert!(errors.has_errors());
        assert_eq!(
            errors.get_field_errors("username").unwrap(),
            &vec!["username is required".to_string()]
        );
    }

    #[test]
    fn validate_required_accepts_trimmed_non_empty_values() {
        let mut errors = FormErrors::new();

        validate_required("  alice  ", "username", &mut errors);

        assert!(!errors.has_errors());
    }

    #[test]
    fn validate_min_length_rejects_short_values() {
        let mut errors = FormErrors::new();

        validate_min_length("short", "password", 8, &mut errors);

        assert!(errors.has_errors());
        assert_eq!(
            errors.get_field_errors("password").unwrap(),
            &vec!["password must be at least 8 characters".to_string()]
        );
    }

    #[test]
    fn validate_min_length_accepts_values_at_threshold() {
        let mut errors = FormErrors::new();

        validate_min_length("12345678", "password", 8, &mut errors);

        assert!(!errors.has_errors());
    }

    #[test]
    fn form_errors_reports_general_and_field_errors() {
        let mut errors = FormErrors::new();
        assert!(!errors.has_errors());

        errors.add_general_error("something went wrong");
        assert!(errors.has_errors());

        let mut field_errors = FormErrors::new();
        field_errors.add_field_error("username", "already exists");
        assert!(field_errors.has_errors());
    }
}
