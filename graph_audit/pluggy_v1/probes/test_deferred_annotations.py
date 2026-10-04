"""Independent public-API regression derived from issue #629, not patch internals."""
import pluggy

def test_register_does_not_evaluate_deferred_annotations():
    namespace = {'hookimpl': pluggy.HookimplMarker('audit')}
    # dont_inherit ensures Python 3.14 native deferred annotations, not strings.
    source = '''
class Plugin:
    @hookimpl
    def event(self, value: MissingAtRuntime) -> None:
        return value
'''
    exec(compile(source, '<issue629>', 'exec', dont_inherit=True), namespace)
    pm = pluggy.PluginManager('audit')
    pm.register(namespace['Plugin']())
    assert pm.hook.event(value=7) == [7]
