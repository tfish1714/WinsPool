"""A static-domain fill that started before a clear/signal must not be kept as current."""
import services.cache_service as cs
import services.data_service as ds


def test_stale_fill_racing_a_clear_is_not_retained(monkeypatch):
    cs.clear_data_cache()
    stale = {"players": "PRE-BUMP"}

    def racing_fetch():
        # The read happened (stale), then a write clears the domain before the store.
        cs.clear_data_cache(cs.DOMAIN_STATIC)
        return stale

    monkeypatch.setattr(ds, "_fetch_static_bucket", racing_fetch)
    assert ds._get_static_bucket() is stale  # caller still gets its data
    assert cs.get_domain(cs.DOMAIN_STATIC) is None  # but it is not cached as current

    fresh = {"players": "POST-BUMP"}
    monkeypatch.setattr(ds, "_fetch_static_bucket", lambda: fresh)
    assert ds._get_static_bucket() is fresh
    assert cs.get_domain(cs.DOMAIN_STATIC) is fresh
    cs.clear_data_cache()


def test_fill_is_stamped_with_fetch_start_time(monkeypatch):
    cs.clear_data_cache()
    monkeypatch.setattr(ds.time, "time", lambda: 1000.0)

    def slow_fetch():
        return {"players": "x"}

    monkeypatch.setattr(ds, "_fetch_static_bucket", slow_fetch)
    ds._get_static_bucket()
    assert cs.get_domain_timestamp(cs.DOMAIN_STATIC) == 1000.0
    cs.clear_data_cache()


def test_clear_all_bumps_generation():
    before = cs.get_domain_generation(cs.DOMAIN_STATIC)
    cs.clear_data_cache()
    assert cs.get_domain_generation(cs.DOMAIN_STATIC) > before
