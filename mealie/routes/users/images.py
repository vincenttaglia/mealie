import shutil
from uuid import uuid4

from fastapi import File, HTTPException, UploadFile, status
from pydantic import UUID4

from mealie.core.config import get_storage
from mealie.core.dependencies import get_temporary_path
from mealie.pkgs import cache, img
from mealie.routes._base import BaseUserController, controller
from mealie.routes._base.routers import UserAPIRouter
from mealie.routes.users._helpers import assert_user_change_allowed
from mealie.schema.user import PrivateUser

router = UserAPIRouter(prefix="", tags=["Users: Images"])


@controller(router)
class UserImageController(BaseUserController):
    @router.post("/{id}/image")
    def update_user_image(
        self,
        id: UUID4,
        profile: UploadFile = File(...),
    ):
        """Updates a User Image"""
        storage = get_storage()
        image_key = f"{PrivateUser.storage_prefix(id)}profile.webp"

        with get_temporary_path() as temp_path:
            assert_user_change_allowed(id, self.user, self.user)

            # use a generated uuid and ignore the filename so we don't
            # need to worry about sanitizing user inputs.
            temp_img = temp_path.joinpath(str(uuid4()))

            with temp_img.open("wb") as buffer:
                shutil.copyfileobj(profile.file, buffer)

            image = img.PillowMinifier.to_webp(temp_img)
            storage.write_file(image_key, image, content_type="image/webp")

        self.repos.users.patch(id, {"cache_key": cache.new_key()})

        if not storage.exists(image_key):
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
