import os,sys
p=sys.argv[1]
print('exists before', os.path.exists(p))
try:
    os.remove(p)
    print('removed')
except Exception as e:
    print('error', e)
print('exists after', os.path.exists(p))
