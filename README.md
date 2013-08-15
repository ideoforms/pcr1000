pcr1000.py
==========

A Python package to interface with the ICOM PCR-1000 serial-controlled radio receiver.

Awaiting cleanup and further doc strings; caveat emptor.

```python
#!/usr/bin/python

import time
import pcr1000

pcr = pcr1000.PCR1000()

# start a connection 
pcr.open()

# assign a callback when our reception signal strength is updated
pcr.on_signal_strength(lambda response, device: print "#" * int(response.args[0] * 150))

# start receiving
pcr.start()

# scan from 88.6Mhz to 105.6Mhz wideband FM in 200hz intervals
for freq in range(int(88e6), int(105e6), 200):
    pcr.tune(freq, PCR1000.MODE_WFM, PCR1000.FLT_230K)
    time.sleep(0.1)
```
