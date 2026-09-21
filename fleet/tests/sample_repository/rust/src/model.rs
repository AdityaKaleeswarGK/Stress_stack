pub struct Basket {
    pub count: u32,
}

impl Basket {
    pub fn new(count: u32) -> Self { Self { count } }
}

pub fn double(value: u32) -> u32 { value * 2 }
