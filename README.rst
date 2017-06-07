Amrita plug-in
=====================

The `LabManager <http://github.com/gateway4labs/labmanager/>`_ provides an API for
supporting more Remote Laboratory Management Systems (RLMS). This project is the
implementation for the `Amrita 
<http://amrita.olabs.edu.in>`_ virtual laboratories.

Usage
-----

First install the module::

  $ pip install git+https://github.com/gateway4labs/rlms_amrita.git

Then add it in the LabManager's ``config.py``::

  RLMS = ['amrita', ... ]

Profit!
