<%!
from carthage.skeleton import package_to_dir
%>\
<%def name='output()'>
%if args.package:
${package_to_dir(args.package)}/layout.py
%else:
python/layout.py
%endif
</%def>\
# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
