"""Agent-identified preservation risk: callable truthiness is not registration."""
import pluggy

def test_registered_falsey_trace_processor_is_called():
    pm = pluggy.PluginManager('audit')
    calls = []
    class Processor:
        def __bool__(self):
            return False
        def __call__(self, tags, args):
            calls.append((tags, args))
    pm.trace.root.setprocessor(('audit',), Processor())
    pm.trace.root.get('audit')('message')
    assert calls == [(('audit',), ('message',))]
