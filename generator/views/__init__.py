"""One module per page family. A view renders its pages ON IMPORT (the
template strings must keep their exact indentation, so the code stays
top-level); build.py imports them in build order, exactly the order the
monolith executed. Views read the model and call render helpers; they
never derive facts of their own."""
