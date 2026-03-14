from . import wishlist, search, votes, admin


def register_all(dp):
    dp.include_router(wishlist.router)
    dp.include_router(search.router)
    dp.include_router(votes.router)
    dp.include_router(admin.router)
