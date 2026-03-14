from . import wishlist, search, votes, admin, games, steam


def register_all(dp):
    dp.include_router(wishlist.router)
    dp.include_router(search.router)
    dp.include_router(votes.router)
    dp.include_router(admin.router)
    dp.include_router(games.router)
    dp.include_router(steam.router)
