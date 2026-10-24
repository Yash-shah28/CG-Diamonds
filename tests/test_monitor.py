from drc_sync.monitor import Stability

def test_debounce_and_atomic_replace():
    state = Stability()
    assert not state.ready('a', (100, 1), 0)
    assert not state.ready('a', (101, 2), 1)
    assert not state.ready('a', (101, 2), 2)
    assert state.ready('a', (101, 2), 3)
    assert not state.ready('a', (99, 3), 4)
    assert state.ready('a', (99, 3), 6)

def test_independent_files_and_polling():
    state = Stability()
    assert not state.ready('natural', (1, 1), 0)
    assert not state.ready('lab', (1, 1), 1)
    assert state.ready('natural', (1, 1), 2)
    assert not state.ready('lab', (1, 1), 2)
    assert state.ready('lab', (1, 1), 3)
