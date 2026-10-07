"""Minimal in-memory Firestore fake for service/script tests."""
import copy


class _Snap:
    def __init__(self, data, doc_id):
        self._d, self.id = data, doc_id
        self.exists = data is not None

    def to_dict(self):
        return None if self._d is None else copy.deepcopy(self._d)


class FakeNotFound(Exception):
    """Mirrors google.api_core.exceptions.NotFound on update() of a missing doc."""


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


class _Doc:
    def __init__(self, store, name, doc_id):
        self.store, self.name, self.id = store, name, doc_id

    def get(self):
        return _Snap(self.store.get(self.name, {}).get(self.id), self.id)

    def set(self, data, merge=False):
        col = self.store.setdefault(self.name, {})
        if merge and self.id in col:
            _merge(col[self.id], data)
        else:
            col[self.id] = copy.deepcopy(data)

    def update(self, data):
        col = self.store.get(self.name, {})
        if self.id not in col:
            raise FakeNotFound(f"No document to update: {self.name}/{self.id}")
        _merge(col[self.id], data)


class _Query:
    def __init__(self, store, name, preds):
        self.store, self.name, self.preds = store, name, preds

    def where(self, filter=None, **kw):
        return _Query(self.store, self.name, self.preds + [filter])

    def stream(self):
        for doc_id, data in list(self.store.get(self.name, {}).items()):
            if all(data.get(p.field_path) == p.value for p in self.preds):
                yield _Snap(data, doc_id)


class _Col(_Query):
    def __init__(self, store, name):
        super().__init__(store, name, [])

    def document(self, doc_id):
        return _Doc(self.store, self.name, str(doc_id))


class FakeFirestore:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return _Col(self.store, name)
