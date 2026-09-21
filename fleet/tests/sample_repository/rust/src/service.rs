use crate::model::{Basket as Box, double as twice};
use std::cmp::max;

pub fn build(count: u32) -> Box {
    Box::new(twice(max(count, 1)))
}

pub mod nested {
    use super::super::model::double as twice;
    pub fn run() -> u32 { twice(4) }
}
