import sys
class CORE_NAME:
    pass
class AUTOFIX_ENV:
    pass
class SKIP_ENV:
    pass
class PinDeclaration:
    pass
class Requirement:
    pass
assert_core_pin = None
canonical_requirement = None
declared_core_pin = None
editable_core_path = None
installed_core_version = None
normalise_name = None
parse_requirement = None
pin_declaration = None
pin_from_requirements = None
pin_problem = None
pin_report = None
recorded_core_version = None
repair_core = None

def enforce_core_pin(dist_name, *, core_preimported, stream=None):
    print("PREIMPORTED:" + repr(core_preimported))
    sys.stdout.flush()
