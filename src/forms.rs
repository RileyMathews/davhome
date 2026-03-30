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
