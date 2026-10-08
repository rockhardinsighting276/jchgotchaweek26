# Gotcha Week
## Docker app
Pull image from ```ghcr.io/rockhardinsighting276/jchgotchaweek26:latest```, see sample ```docker-compose.yml```


## Non-docker app
Clone with ```git clone```

Setup using 

```pip install -r requirements.txt```


Setup admin secret using
 
```
export ADMIN_SECRET=<your secret>
```

Run using
```
uvicorn app:app --reload
```
On root directory 
