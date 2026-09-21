use orchard_map::service::{build, nested};
use orchard_map::Basket;

#[test]
fn imported_paths_work() {
    let basket: Basket = build(3);
    assert_eq!(basket.count, 6);
    assert_eq!(nested::run(), 8);
}
