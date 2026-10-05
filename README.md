# Gotcha Week
## Docker app
Pull image from ```ghcr.io```

Change environment variables as needed


## Non-docker app
Clone with ```git clone```

Setup using 

```pip install -r requirements.txt```


setup admin secret using
 
```
export ADMIN_SECRET=<your secret>
```

run using
```
uvicorn app:app --reload
```
on root directory 

Setup page:
![](https://i.ibb.co/zVGFRfGj/image.png)

Admin should put in the secret in the fields to enter 
Admin Page: 
put in names here: 
![](https://i.ibb.co/ymYyFS7p/image.png)
and click on start. untick shuffle for your own custom order. 
