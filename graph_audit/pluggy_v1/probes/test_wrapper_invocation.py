"""Agent-suggested public hook-call regression, implemented for the audit."""
import pluggy
import pytest

def test_wrapper_factory_exception_propagates():
    pm = pluggy.PluginManager('audit')
    hookspec = pluggy.HookspecMarker('audit')
    hookimpl = pluggy.HookimplMarker('audit')
    class Spec:
        @hookspec
        def event(self, value):
            pass
    class Plugin:
        @hookimpl(wrapper=True)
        def event(self, value):
            if value == 0:
                raise StopIteration('wrapper factory rejected input')
            def implementation():
                result = yield
                return result
            return implementation()
    pm.add_hookspecs(Spec)
    pm.register(Plugin())
    with pytest.raises(StopIteration, match='wrapper factory rejected input'):
        pm.hook.event(value=0)
